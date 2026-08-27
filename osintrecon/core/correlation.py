"""Entity correlation engine.

Groups findings/identifiers that likely belong to the same real-world actor.
Two identifiers are linked when:
  - they were both supplied as seeds in the same run, or
  - one was *discovered* as metadata while investigating the other
    (e.g. a GitHub profile's public email), or
  - the same normalized username string appears across independently
    investigated seeds (exact-match heuristic).

This is deliberately conservative: it links on exact/derived identifiers
only, never on fuzzy name similarity, to keep false-positive correlation low.
"""
from __future__ import annotations

from collections import defaultdict

from osintrecon.core.models import Entity, Finding, Identifier


class CorrelationEngine:
    def __init__(self) -> None:
        self._parent: dict[Identifier, Identifier] = {}

    def _find(self, ident: Identifier) -> Identifier:
        self._parent.setdefault(ident, ident)
        while self._parent[ident] != ident:
            self._parent[ident] = self._parent[self._parent[ident]]
            ident = self._parent[ident]
        return ident

    def _union(self, a: Identifier, b: Identifier) -> None:
        ra, rb = self._find(a), self._find(b)
        if ra != rb:
            self._parent[rb] = ra

    def correlate(self, findings: list[Finding]) -> list[Entity]:
        # Seed union-find with every identifier seen (seed + discovered).
        for finding in findings:
            self._find(finding.identifier)
            for discovered in finding.discovered_identifiers:
                self._find(discovered)
                self._union(finding.identifier, discovered)

        # Also union identical username strings discovered independently
        # across different seed identifiers (e.g. two emails that both
        # resolve to a GitHub account with the same username via metadata).
        by_value: dict[tuple, list[Identifier]] = defaultdict(list)
        for ident in list(self._parent.keys()):
            by_value[(ident.type, ident.value)].append(ident)
        for group in by_value.values():
            for other in group[1:]:
                self._union(group[0], other)

        clusters: dict[Identifier, Entity] = {}
        for finding in findings:
            root = self._find(finding.identifier)
            entity = clusters.setdefault(root, Entity())
            entity.add_finding(finding)

        return list(clusters.values())
