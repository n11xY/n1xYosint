"""ORCID source module -- uses the official, free, keyless ORCID public
API to check whether a full name matches a registered researcher iD.

ORCID is a real, globally unique researcher identifier (like a DOI, but
for people) -- a match is a genuinely useful cross-reference, but it's
still only a NAME match on ORCID's index, not identity confirmation
(common names, or a namesake academic, can collide). Always
MatchStatus.UNCERTAIN, same reasoning as github_name_search.py.

Live-verified: "Yann LeCun" (given-names + family-name) returns real
ORCID iDs; a nonsense name returns `num-found: 0`.

Tries the exact name first; if that returns nothing and the name has
diacritics, retries once with the ASCII-folded form (name_variants.py)
-- ORCID's index is not reliably diacritic-tolerant for non-Latin-heavy
scripts like Turkish's.
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core import name_variants
from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://pub.orcid.org/v3.0/search"
MAX_RESULTS = 5


def _split_name(name: str) -> tuple[str, str]:
    """ORCID's search index wants given-names/family-name separately.
    Best-effort split: everything but the last word is "given", the
    last word is "family" -- matches how normalize.py already treats a
    NAME identifier (2+ space-separated words), no new assumption here."""
    parts = name.split()
    if len(parts) < 2:
        return name, ""
    return " ".join(parts[:-1]), parts[-1]


class OrcidPlugin(SourcePlugin):
    name: ClassVar[str] = "orcid"
    category: ClassVar[str] = "academic"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.NAME}
    description: ClassVar[str] = (
        "Checks the official, free, keyless ORCID public API for a matching researcher iD -- "
        "always uncertain, a name match alone is never proof of identity."
    )

    async def run(self, identifier: Identifier) -> list[Finding]:
        results: list = []
        candidate_name = identifier.value
        resp = None

        for candidate_name in name_variants.variants(identifier.value):
            given, family = _split_name(candidate_name)
            if not family:
                return []  # ORCID's search needs at least a given+family split
            query = f"given-names:{given} AND family-name:{family}"
            resp = await self.http.get(self.name, API_URL, headers={"Accept": "application/json"}, params={"q": query})

            if resp.error is not None:
                return [Finding(
                    source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                    source_url=API_URL, title="ORCID API request failed", category=self.category,
                    metadata={"error": resp.error},
                )]
            if resp.status != 200:
                return [Finding(
                    source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                    source_url=API_URL, title=f"ORCID API returned {resp.status}", category=self.category,
                    metadata={"http_status": resp.status},
                )]

            results = (resp.json() or {}).get("result") or []
            if results:
                break  # exact form matched -- no need to try the ASCII-folded variant

        if not results or resp is None:
            return []

        findings = []
        for item in results[:MAX_RESULTS]:
            orcid_id = (item.get("orcid-identifier") or {}).get("path")
            if not orcid_id:
                continue
            findings.append(Finding(
                source=self.name,
                identifier=identifier,
                status=MatchStatus.UNCERTAIN,
                source_url=f"https://orcid.org/{orcid_id}",
                title=f"ORCID researcher iD: {orcid_id}",
                category=self.category,
                metadata={"orcid_id": orcid_id, "matched_query": candidate_name},
                evidence_path=resp.evidence_path,
            ))
        return findings
