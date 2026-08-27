"""Enumeration engine: orchestrates concurrent execution of source plugins
across every identifier, collects findings, and drives dedup/scoring/correlation.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from osintrecon.core.cache import ResponseCache
from osintrecon.core.config import Config
from osintrecon.core.correlation import CorrelationEngine
from osintrecon.core.http_client import AsyncHttpClient
from osintrecon.core.logging_setup import get_logger
from osintrecon.core.models import Entity, Finding, Identifier, RunStats
from osintrecon.core.scoring import process as dedup_and_score
from osintrecon.plugins.registry import PluginRegistry

log = get_logger("engine")


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

    async def run(self, identifiers: list[Identifier]) -> RunResult:
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

            async def bound_run(plugin, identifier: Identifier) -> list[Finding]:
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
            cache.close()

        all_findings: list[Finding] = [f for sub in results_nested for f in sub]
        scored, duplicates_removed = dedup_and_score(all_findings)

        entities = CorrelationEngine().correlate(scored)

        self.stats.findings_total = len(scored)
        self.stats.duplicates_removed = duplicates_removed
        self.stats.end_time = self.stats.end_time or time.time()

        return RunResult(findings=scored, entities=entities, stats=self.stats)
