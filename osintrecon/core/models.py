"""Core data models shared across the framework.

Every piece of evidence the engine collects is represented as a `Finding`,
always carrying the exact source URL/API endpoint it came from so results
remain auditable back to their origin.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class IdentifierType(str, Enum):
    USERNAME = "username"
    EMAIL = "email"
    DOMAIN = "domain"
    URL = "url"
    NAME = "name"
    PHONE = "phone"
    OTHER = "other"


class MatchStatus(str, Enum):
    """How confident the source module is that a result is a genuine match."""

    CONFIRMED = "confirmed"      # Positive, unambiguous signal (e.g. API returned the exact account)
    PROBABLE = "probable"        # Strong heuristic match, some residual uncertainty
    UNCERTAIN = "uncertain"      # Weak/heuristic signal only, needs human review
    NOT_FOUND = "not_found"      # Source was queried, no match
    ERROR = "error"              # Source could not be queried (network/auth/rate-limit failure)


@dataclass(slots=True)
class Identifier:
    """A single seed input (username, email, ...) after normalization."""

    value: str
    type: IdentifierType
    # Original, pre-normalization input as supplied by the user. Excluded from
    # equality/hash: two identifiers with the same (type, value) are the same
    # logical identity regardless of how each was originally typed/discovered,
    # and equality must stay consistent with __hash__ below.
    raw: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        if not self.raw:
            self.raw = self.value

    def __hash__(self) -> int:
        return hash((self.type, self.value))


@dataclass(slots=True)
class Finding:
    """A single piece of evidence discovered by a source module."""

    source: str                              # plugin/module name, e.g. "github"
    identifier: Identifier                   # the seed identifier this finding relates to
    status: MatchStatus
    source_url: str                          # exact URL/endpoint the data came from
    title: str = ""                          # short human-readable summary
    category: str = "general"                # e.g. "social", "code-hosting", "breach", "paste"
    confidence: float = 0.0                  # 0.0-1.0, set by the scoring engine
    metadata: dict[str, Any] = field(default_factory=dict)
    discovered_identifiers: list[Identifier] = field(default_factory=list)
    evidence_path: Optional[str] = None      # path to saved raw response, if --save-evidence
    timestamp: float = field(default_factory=time.time)
    finding_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def dedup_key(self) -> tuple:
        return (self.source, self.identifier.type, self.identifier.value, self.source_url)


@dataclass(slots=True)
class Entity:
    """A correlated cluster of identifiers/findings believed to belong to one actor."""

    entity_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    identifiers: set[Identifier] = field(default_factory=set)
    findings: list[Finding] = field(default_factory=list)
    confidence: float = 0.0

    def add_finding(self, finding: Finding) -> None:
        self.findings.append(finding)
        self.identifiers.add(finding.identifier)
        for ident in finding.discovered_identifiers:
            self.identifiers.add(ident)


@dataclass(slots=True)
class RunStats:
    """Execution statistics for a single framework run."""

    sources_queried: int = 0
    requests_sent: int = 0
    requests_succeeded: int = 0
    requests_failed: int = 0
    findings_total: int = 0
    duplicates_removed: int = 0
    cache_hits: int = 0
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None

    @property
    def elapsed(self) -> float:
        return (self.end_time or time.time()) - self.start_time

    def to_dict(self) -> dict[str, Any]:
        return {
            "sources_queried": self.sources_queried,
            "requests_sent": self.requests_sent,
            "requests_succeeded": self.requests_succeeded,
            "requests_failed": self.requests_failed,
            "findings_total": self.findings_total,
            "duplicates_removed": self.duplicates_removed,
            "cache_hits": self.cache_hits,
            "elapsed_seconds": round(self.elapsed, 2),
        }
