"""Identity-resolution confidence scoring for correlated entities.

correlation.py's union-find already groups findings/identifiers it's
confident belong to one actor (exact-identifier matches, plus
avatar_correlation.py's narrow, documented exception for a strict
perceptual-photo-hash match). This module never merges or drops
anything -- it only *annotates* those already-built entities with a
confidence score and a human-readable "why" list, the same posture as
avatar_correlation.py: a real signal, always something a human still
weighs, never an automatic "these are definitely the same person."

Entities with a single identifier are left alone (confidence 0.0, no
reasons) -- correlation.py just carried a seed identifier through
untouched, there's nothing to resolve.
"""
from __future__ import annotations

from osintrecon.core.models import Entity, MatchStatus
from osintrecon.core.scoring import plugin_family

MAX_CONFIDENCE = 0.99

# Metadata keys that actually appear, with genuinely comparable meaning,
# on more than one plugin's Finding.metadata in this codebase -- checked
# by grepping osintrecon/plugins/sources before adding a key here, not
# assumed. A field only one plugin ever populates isn't a cross-source
# signal, it's noise, so it isn't in this list.
COMPARABLE_METADATA_KEYS = ("company", "location", "country")


def confidence_bucket(score: float) -> str:
    if score >= 0.90:
        return "very strong"
    if score >= 0.75:
        return "strong"
    if score >= 0.55:
        return "possible"
    if score >= 0.30:
        return "weak"
    return "very weak"


def _shared_metadata_reasons(findings: list) -> tuple[float, list[str]]:
    """+0.15 and a reason for each metadata key where two *different*
    plugin families independently reported the same (case-insensitive,
    exact) value. No fuzzy similarity -- that's exactly the kind of
    thing that quietly merges different people who happen to work at
    similarly-named places."""
    score = 0.0
    reasons: list[str] = []

    for key in COMPARABLE_METADATA_KEYS:
        value_to_family: dict[str, str] = {}
        for f in findings:
            value = f.metadata.get(key)
            if not value or not isinstance(value, str):
                continue
            family = plugin_family(f.source)
            normalized = value.strip().lower()
            if not normalized:
                continue
            existing_family = value_to_family.get(normalized)
            if existing_family and existing_family != family:
                score += 0.15
                reasons.append(f"Same {key} ({value.strip()}) reported by {existing_family} and {family}")
            else:
                value_to_family.setdefault(normalized, family)

    return score, reasons


def score_entity(entity: Entity) -> None:
    """Sets entity.confidence and entity.reasons in place."""
    if len(entity.identifiers) < 2:
        return  # a lone seed identifier, not a cross-source match -- nothing to resolve

    findings = [f for f in entity.findings if f.status not in (MatchStatus.NOT_FOUND, MatchStatus.ERROR)]
    if not findings:
        return

    families = sorted({plugin_family(f.source) for f in findings})
    confirmed = [f for f in findings if f.status == MatchStatus.CONFIRMED]

    score = 0.0
    reasons: list[str] = []

    # Independent-source corroboration: the same idea scoring.py already
    # rewards per-finding, applied at the entity level -- how many
    # *different* plugins linked into this cluster, not how many
    # findings (one plugin checking 90 sites is one opinion, not 90).
    if len(families) >= 2:
        score += min(0.4, 0.15 * len(families))
        reasons.append(f"Linked by {len(families)} independent sources ({', '.join(families)})")

    if confirmed:
        score += min(0.3, 0.15 * len(confirmed))
        plural = "s" if len(confirmed) != 1 else ""
        reasons.append(f"Confirmed on {len(confirmed)} source{plural} via official API{plural}")

    meta_score, meta_reasons = _shared_metadata_reasons(findings)
    score += meta_score
    reasons.extend(meta_reasons)

    if any(f.source == "avatar_correlation" for f in findings):
        score += 0.2
        reasons.append("Profile photo closely matches another linked account")

    entity.confidence = min(MAX_CONFIDENCE, round(score, 3))
    entity.reasons = reasons


def score_entities(entities: list[Entity]) -> list[Entity]:
    for entity in entities:
        score_entity(entity)
    return entities
