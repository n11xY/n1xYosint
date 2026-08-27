"""Deduplication and confidence-scoring engine.

Deduplication: findings are considered duplicates when they share
(source, identifier, source_url) -- the same module reporting the same
evidence for the same identifier twice (e.g. from a cache hit + live re-run
merge, or a retried request that didn't get de-duplicated upstream).

Scoring: maps each finding's MatchStatus to a base confidence score, then
applies small adjustments:
  - cached results are not penalized (evidence is still valid)
  - findings corroborated by a second, independent source for the same
    identifier get a small boost (cross-source corroboration)
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


def score(findings: list[Finding]) -> list[Finding]:
    """Assigns confidence scores in place and returns the same list."""
    by_identifier: dict[tuple, set[str]] = defaultdict(set)
    for f in findings:
        if f.status not in (MatchStatus.NOT_FOUND, MatchStatus.ERROR):
            by_identifier[(f.identifier.type, f.identifier.value)].add(f.source)

    for f in findings:
        base = BASE_CONFIDENCE.get(f.status, 0.0)
        sources_for_ident = by_identifier[(f.identifier.type, f.identifier.value)]
        corroboration = max(0, len(sources_for_ident) - 1) * CORROBORATION_BOOST
        f.confidence = min(MAX_CONFIDENCE, round(base + corroboration, 3)) if base > 0 else 0.0

    return findings


def process(findings: list[Finding]) -> tuple[list[Finding], int]:
    """Full pipeline: dedup then score. Returns (findings, duplicates_removed)."""
    deduped, removed = deduplicate(findings)
    scored = score(deduped)
    return scored, removed
