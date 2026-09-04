"""Crossref source module -- uses the official, free, keyless Crossref
API to check whether a full name matches a published work's author list.

Crossref indexes DOI-registered scholarly output (journal articles,
conference papers, ...); a match surfaces real, citable publications --
still just a name match on the author field, not identity confirmation
(the same name can belong to different authors, especially with only
given+family name to go on, no institution disambiguation available from
this endpoint alone). Always MatchStatus.UNCERTAIN, same reasoning as
orcid.py/openalex.py.

Live-verified: "query.author=Yann LeCun" returns real, DOI-bearing
publications. Crossref author-name queries can match tens of thousands
of loosely-related works (many false positives on common surnames), so
results are capped hard and sorted isn't attempted -- this is a breadth
signal, not a ranked one. Tries the exact name first, falls back once to
the ASCII-folded form (name_variants.py) if that returns nothing.
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core import name_variants
from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://api.crossref.org/works"
MAX_RESULTS = 3
MAX_RESULTS_BY_DEPTH = {"quick": 1, "normal": MAX_RESULTS, "deep": 6}


class CrossrefPlugin(SourcePlugin):
    name: ClassVar[str] = "crossref"
    category: ClassVar[str] = "academic"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.NAME}
    description: ClassVar[str] = (
        "Checks the official, free, keyless Crossref API for publications matching a name -- "
        "always uncertain, a name match alone is never proof of identity."
    )

    async def run(self, identifier: Identifier) -> list[Finding]:
        search_depth = self.config.get("search_depth", "normal")
        max_results = MAX_RESULTS_BY_DEPTH.get(search_depth, MAX_RESULTS)

        items: list = []
        resp = None

        for candidate_name in name_variants.variants(identifier.value, deep=(search_depth == "deep")):
            params = {
                "query.author": candidate_name,
                "rows": max_results,
                "select": "title,DOI,published,container-title,author",
            }
            resp = await self.http.get(self.name, API_URL, params=params)

            if resp.error is not None:
                return [Finding(
                    source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                    source_url=API_URL, title="Crossref API request failed", category=self.category,
                    metadata={"error": resp.error},
                )]
            if resp.status != 200:
                return [Finding(
                    source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                    source_url=API_URL, title=f"Crossref API returned {resp.status}", category=self.category,
                    metadata={"http_status": resp.status},
                )]

            items = ((resp.json() or {}).get("message") or {}).get("items") or []
            if items:
                break

        if not items or resp is None:
            return []

        findings = []
        for work in items[:max_results]:
            title = (work.get("title") or [None])[0]
            if not title:
                continue
            doi = work.get("DOI")
            venue = (work.get("container-title") or [None])[0]
            year = ((work.get("published") or {}).get("date-parts") or [[None]])[0][0]

            findings.append(Finding(
                source=self.name,
                identifier=identifier,
                status=MatchStatus.UNCERTAIN,
                source_url=f"https://doi.org/{doi}" if doi else API_URL,
                title=f"Crossref publication: {title}",
                category=self.category,
                metadata={"doi": doi, "venue": venue, "year": year},
                evidence_path=resp.evidence_path,
            ))
        return findings
