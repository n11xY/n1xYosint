"""Deduplication and confidence-scoring engine.

Deduplication: findings are considered duplicates when they share
(source, identifier, source_url) -- the same module reporting the same
evidence for the same identifier twice (e.g. from a cache hit + live re-run
merge, or a retried request that didn't get de-duplicated upstream).

Scoring: maps each finding's MatchStatus to a base confidence score, then
applies small adjustments:
  - cached results are not penalized (evidence is still valid)
  - findings corroborated by a second, independent *plugin* for the same
    identifier get a small boost (cross-source corroboration)

Corroboration is counted per plugin family (e.g. "username_sites"), not per
individual site checked by a plugin. A username being registered on ten
unrelated platforms doesn't make each of those ten matches more likely to be
the *same person* -- it's one plugin's opinion, not ten independent ones.
Counting it as ten would artificially inflate confidence for exactly the
kind of coincidental username reuse this scoring is meant to flag as
uncertain, not confirm.
"""
from __future__ import annotations

from collections import defaultdict

from osintrecon.core.models import Finding, MatchStatus

BASE_CONFIDENCE = {
    MatchStatus.CONFIRMED: 0.95,
    MatchStatus.PROBABLE: 0.7,
    MatchStatus.UNCERTAIN: 0.35,
    MatchStatus.NOT_FOUND: 0.0,
    MatchStatus.ERROR: 0.0,
}

CORROBORATION_BOOST = 0.05
MAX_CONFIDENCE = 0.99


def deduplicate(findings: list[Finding]) -> tuple[list[Finding], int]:
    seen: dict[tuple, Finding] = {}
    removed = 0
    for finding in findings:
        key = finding.dedup_key()
        if key in seen:
            removed += 1
            continue
        seen[key] = finding
    return list(seen.values()), removed


def _plugin_family(source: str) -> str:
    """'username_sites:Instagram' and 'username_sites:GitHub' are the same
    plugin's opinion, not two independent ones -- group by the part before ':'."""
    return source.split(":", 1)[0]


def score(findings: list[Finding]) -> list[Finding]:
    """Assigns confidence scores in place and returns the same list."""
    by_identifier: dict[tuple, set[str]] = defaultdict(set)
    for f in findings:
        if f.status not in (MatchStatus.NOT_FOUND, MatchStatus.ERROR):
            by_identifier[(f.identifier.type, f.identifier.value)].add(_plugin_family(f.source))

    for f in findings:
        base = BASE_CONFIDENCE.get(f.status, 0.0)
        plugin_families = by_identifier[(f.identifier.type, f.identifier.value)]
        corroboration = max(0, len(plugin_families) - 1) * CORROBORATION_BOOST
        f.confidence = min(MAX_CONFIDENCE, round(base + corroboration, 3)) if base > 0 else 0.0

    return findings


def process(findings: list[Finding]) -> tuple[list[Finding], int]:
    """Full pipeline: dedup then score. Returns (findings, duplicates_removed)."""
    deduped, removed = deduplicate(findings)
    scored = score(deduped)
    return scored, removed
