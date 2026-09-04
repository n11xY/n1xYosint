"""Enumeration engine: orchestrates concurrent execution of source plugins
across every identifier, collects findings, and drives dedup/scoring/correlation.

Supports optional multi-hop enrichment: an identifier discovered while
investigating a seed (e.g. an email pulled from a GitHub bio) can itself be
queued for investigation in a subsequent round, up to a configurable depth.
Already-visited identifiers are never re-queried, which also rules out
cycles (A discovers B, B discovers A, ...).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from osintrecon.core.avatar_correlation import find_avatar_matches
from osintrecon.core.cache import ResponseCache
from osintrecon.core.config import Config
from osintrecon.core.correlation import CorrelationEngine
from osintrecon.core.entity_scoring import score_entities
from osintrecon.core.http_client import AsyncHttpClient
from osintrecon.core.logging_setup import get_logger
from osintrecon.core.models import Entity, Finding, Identifier, RunStats
from osintrecon.core.scoring import process as dedup_and_score
from osintrecon.plugins.base import SourcePlugin
from osintrecon.plugins.registry import PluginRegistry

log = get_logger("engine")

DEFAULT_MAX_ENRICHMENT_IDENTIFIERS = 200


@dataclass
class RunResult:
    findings: list[Finding] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)
    stats: RunStats = field(default_factory=RunStats)
    rejected_inputs: list[tuple[str, str]] = field(default_factory=list)


class Engine:
    def __init__(self, config: Config):
        self.config = config
        self.stats = RunStats()

    async def run(self, identifiers: list[Identifier], depth: int = 1) -> RunResult:
        """Run the full pipeline. `depth` is how many enrichment rounds to do:
        1 = only the seed identifiers (default, matches prior behavior),
        2+ = also investigate identifiers discovered along the way, that many
        rounds deep."""
        cache = ResponseCache(
            path=self.config.get("cache.path"),
            ttl_seconds=self.config.get("cache.ttl_seconds", 86400),
            enabled=self.config.get("cache.enabled", True),
        )

        async with AsyncHttpClient(
            timeout_seconds=self.config.get("timeout_seconds", 10),
            retries=self.config.get("retries", 2),
            retry_backoff=self.config.get("retry_backoff", 1.5),
            user_agent=self.config.get("user_agent"),
            proxy=self.config.get("proxy"),
            default_headers=self.config.get("headers", {}),
            cache=cache,
            rate_limit_per_source=self.config.get("rate_limit_per_source", 5),
            stats=self.stats,
            save_evidence=self.config.get("evidence.save_raw", False),
            evidence_dir=self.config.get("evidence.path"),
        ) as http:
            registry = PluginRegistry(self.config, http).discover()
            plugins = registry.instantiate_enabled()
            self.stats.sources_queried = len(plugins)
            log.info("loaded %d enabled source module(s): %s", len(plugins), ", ".join(p.name for p in plugins))

            overall_concurrency = asyncio.Semaphore(self.config.get("concurrency", 20))
            max_enrichment = self.config.get("max_enrichment_identifiers", DEFAULT_MAX_ENRICHMENT_IDENTIFIERS)

            all_findings: list[Finding] = []
            visited: set[Identifier] = set()
            current_round = list(identifiers)
            hop = 0

            while current_round and hop < max(1, depth):
                visited.update(current_round)

                round_findings = await self._run_round(current_round, plugins, overall_concurrency)
                for f in round_findings:
                    f.hop = hop
                all_findings.extend(round_findings)

                next_round = self._collect_new_identifiers(round_findings, visited, max_enrichment)

                hop += 1
                self.stats.enrichment_identifiers_discovered += len(next_round)
                current_round = next_round if hop < max(1, depth) else []

            self.stats.enrichment_hops_completed = hop

            # Cross-references collected avatar URLs (github/gravatar so far)
            # for near-identical profile photos across otherwise-unlinked
            # identifiers -- a silent no-op if Pillow/imagehash aren't
            # installed (optional dependency, see avatar_correlation.py).
            # Has to run before the http client/cache close, since it needs
            # to fetch the images.
            all_findings.extend(await find_avatar_matches(all_findings, http))

            cache.close()

        scored, duplicates_removed = dedup_and_score(all_findings)
        entities = score_entities(CorrelationEngine().correlate(scored))

        self.stats.findings_total = len(scored)
        self.stats.duplicates_removed = duplicates_removed
        self.stats.end_time = self.stats.end_time or time.time()

        return RunResult(findings=scored, entities=entities, stats=self.stats)

    @staticmethod
    def _collect_new_identifiers(
        findings: list[Finding], visited: set[Identifier], max_enrichment: int
    ) -> list[Identifier]:
        """Gathers not-yet-seen discovered_identifiers from this round's
        findings, deduped, capped at max_enrichment total (visited + new)."""
        next_round: list[Identifier] = []
        seen_this_round: set[Identifier] = set()

        for f in findings:
            for discovered in f.discovered_identifiers:
                if discovered in visited or discovered in seen_this_round:
                    continue
                if len(visited) + len(next_round) >= max_enrichment:
                    log.warning("enrichment cap (%d identifiers) reached, stopping expansion", max_enrichment)
                    return next_round
                seen_this_round.add(discovered)
                next_round.append(discovered)

        return next_round

    async def _run_round(
        self,
        identifiers: list[Identifier],
        plugins: list[SourcePlugin],
        overall_concurrency: asyncio.Semaphore,
    ) -> list[Finding]:
        async def bound_run(plugin: SourcePlugin, identifier: Identifier) -> list[Finding]:
            if not plugin.supports(identifier):
                return []
            async with overall_concurrency:
                try:
                    return await plugin.run(identifier)
                except Exception as exc:  # noqa: BLE001 - isolate plugin failures from the run
                    log.error("plugin %s crashed on %s: %s", plugin.name, identifier.value, exc, exc_info=True)
                    return []

        tasks = [
            bound_run(plugin, identifier)
            for identifier in identifiers
            for plugin in plugins
        ]
        results_nested = await asyncio.gather(*tasks) if tasks else []
        return [f for sub in results_nested for f in sub]
