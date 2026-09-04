"""OpenAlex source module -- uses the official, free, keyless OpenAlex
API to check whether a full name matches an indexed researcher.

OpenAlex indexes ~250M+ scholarly works and their authors; a name match
here is a real cross-reference (and sometimes already links an ORCID
iD), but still just a name match -- always MatchStatus.UNCERTAIN, same
reasoning as orcid.py/github_name_search.py.

Live-verified: "Yann LeCun" returns a real author record (works_count,
h_index, affiliations, and a linked ORCID). Tries the exact name first,
falls back once to the ASCII-folded form (name_variants.py) if that
returns nothing.
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core import name_variants
from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://api.openalex.org/authors"
MAX_RESULTS = 5
MAX_RESULTS_BY_DEPTH = {"quick": 2, "normal": MAX_RESULTS, "deep": 10}


class OpenAlexPlugin(SourcePlugin):
    name: ClassVar[str] = "openalex"
    category: ClassVar[str] = "academic"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.NAME}
    description: ClassVar[str] = (
        "Checks the official, free, keyless OpenAlex API for a matching indexed researcher -- "
        "always uncertain, a name match alone is never proof of identity."
    )

    async def run(self, identifier: Identifier) -> list[Finding]:
        search_depth = self.config.get("search_depth", "normal")
        max_results = MAX_RESULTS_BY_DEPTH.get(search_depth, MAX_RESULTS)

        results: list = []
        resp = None

        for candidate_name in name_variants.variants(identifier.value, deep=(search_depth == "deep")):
            resp = await self.http.get(self.name, API_URL, params={"search": candidate_name, "per_page": max_results})

            if resp.error is not None:
                return [Finding(
                    source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                    source_url=API_URL, title="OpenAlex API request failed", category=self.category,
                    metadata={"error": resp.error},
                )]
            if resp.status != 200:
                return [Finding(
                    source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                    source_url=API_URL, title=f"OpenAlex API returned {resp.status}", category=self.category,
                    metadata={"http_status": resp.status},
                )]

            results = (resp.json() or {}).get("results") or []
            if results:
                break

        if not results or resp is None:
            return []

        findings = []
        for author in results[:max_results]:
            affiliations = author.get("affiliations") or []
            latest_institution = (affiliations[0].get("institution") or {}) if affiliations else {}
            orcid_url = author.get("orcid")

            findings.append(Finding(
                source=self.name,
                identifier=identifier,
                status=MatchStatus.UNCERTAIN,
                source_url=author.get("id", API_URL),
                title=f"OpenAlex researcher: {author.get('display_name', identifier.value)}",
                category=self.category,
                metadata={
                    "works_count": author.get("works_count"),
                    "cited_by_count": author.get("cited_by_count"),
                    "h_index": (author.get("summary_stats") or {}).get("h_index"),
                    "institution": latest_institution.get("display_name"),
                    "country": latest_institution.get("country_code"),
                    "orcid_url": orcid_url,
                },
                # Cross-references its own linked ORCID profile URL when
                # present -- feeds --depth enrichment into wayback.py
                # (the only plugin that currently accepts IdentifierType.URL)
                # for that researcher's archive history. Not fed to
                # orcid.py: that plugin takes a NAME and searches by it, it
                # doesn't look up a specific already-known ORCID iD.
                discovered_identifiers=(
                    [Identifier(value=orcid_url, type=IdentifierType.URL)] if orcid_url else []
                ),
                evidence_path=resp.evidence_path,
            ))
        return findings
