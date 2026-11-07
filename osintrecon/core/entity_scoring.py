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


def _evidence_reasons(entity: Entity, evidence, rarity) -> tuple[float, list[str]]:
    """Account-level evidence (core/account_evidence.py) and username
    rarity (core/username_rarity.py) as score and reasons."""
    from osintrecon.core.account_evidence import node_of

    score = 0.0
    reasons: list[str] = []
    nodes = {node_of(f) for f in entity.findings if f.status in (MatchStatus.CONFIRMED, MatchStatus.PROBABLE)}
    if evidence is not None and nodes:
        tied_links = [lk for lk in evidence.tied_links() if lk.a in nodes and lk.b in nodes]
        strong = [lk for lk in tied_links if lk.strength == "strong"]
        if tied_links:
            tied_nodes = {n for lk in tied_links for n in (lk.a, lk.b)}
            score += min(0.3, 0.05 * len(tied_nodes)) + (0.1 if strong else 0.0)
            reasons.append(f"{len(tied_nodes)} of {len(nodes)} found accounts are tied together by evidence")
            for lk in tied_links[:4]:
                reasons.append(lk.detail)
        conflicts = [c for c in evidence.conflicts if c.a in nodes and c.b in nodes]
        if conflicts:
            score -= min(0.2, 0.1 * len(conflicts))
            for c in conflicts[:2]:
                reasons.append(f"Contradiction: {c.detail}")
    if rarity:
        for ident in sorted(entity.identifiers, key=lambda i: i.value):
            r = rarity.get(ident.value.lower()) if ident.type.value == "username" else None
            if r is None:
                continue
            if r.label == "rare":
                score += 0.1
                reasons.append(f"Username '{r.username}' looks distinctive (about {r.bits:.0f} bits): "
                               "the same name elsewhere is more likely the same person")
            elif r.label == "common":
                score -= 0.1
                share = f", taken on {r.hits} of {r.checked} sites checked" if r.checked else ""
                reasons.append(f"Username '{r.username}' is common (about {r.bits:.0f} bits{share}): "
                               "the same name elsewhere is often someone else")
    return score, reasons


def score_entity(entity: Entity, evidence=None, rarity=None) -> None:
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

    extra_score, extra_reasons = _evidence_reasons(entity, evidence, rarity)
    score += extra_score
    reasons.extend(extra_reasons)

    entity.confidence = max(0.0, min(MAX_CONFIDENCE, round(score, 3)))
    entity.reasons = reasons


def score_entities(entities: list[Entity], evidence=None, rarity=None) -> list[Entity]:
    for entity in entities:
        score_entity(entity, evidence=evidence, rarity=rarity)
    return entities
