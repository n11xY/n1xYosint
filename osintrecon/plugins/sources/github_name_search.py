"""GitHub name-search source module -- uses GitHub's public commit/user
search API (search/users, `in:name` qualifier) to find accounts whose
DISPLAY NAME (not login username) matches a full name. This is the
counterpart to github.py (which looks up a known username): this one
goes the other direction, name -> candidate accounts.

A name match is not proof of identity -- lots of people share a name,
and this only confirms GitHub has an account with a matching display
name, not that it's the specific person being investigated. Always
reported as UNCERTAIN, matching how search_api.py already treats any
NAME-based hit ("a keyword hit isn't proof of identity"). Each match's
username is fed into discovered_identifiers so --depth enrichment can
pull it through github.py/github_commit_email.py/wayback.py for a real,
per-account CONFIRMED signal.

Live-verified: querying "Linus Torvalds" correctly surfaces the login
"torvalds" among 13 results.

Tries the exact name first; if that returns no items, retries once with
the ASCII-folded form (name_variants.py) -- GitHub's display-name index
is not reliably diacritic-tolerant for names like Turkish ones.
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core import name_variants
from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

SEARCH_URL = "https://api.github.com/search/users"
MAX_RESULTS = 5
MAX_RESULTS_BY_DEPTH = {"quick": 2, "normal": MAX_RESULTS, "deep": 10}


class GitHubNameSearchPlugin(SourcePlugin):
    name: ClassVar[str] = "github_name_search"
    category: ClassVar[str] = "code-hosting"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.NAME}
    description: ClassVar[str] = (
        "Searches GitHub's public user-search API for accounts whose display name matches "
        "a full name -- always UNCERTAIN, a name match alone is never proof of identity."
    )

    async def run(self, identifier: Identifier) -> list[Finding]:
        headers = {"Accept": "application/vnd.github+json"}
        token = self.config.get("api_key")
        if token:
            headers["Authorization"] = f"Bearer {token}"

        search_depth = self.config.get("search_depth", "normal")
        max_results = MAX_RESULTS_BY_DEPTH.get(search_depth, MAX_RESULTS)

        items: list = []
        for candidate_name in name_variants.variants(identifier.value, deep=(search_depth == "deep")):
            params = {"q": f'"{candidate_name}" in:name', "per_page": max_results}
            resp = await self.http.get(self.name, SEARCH_URL, headers=headers, params=params, expected_statuses={403, 422})

            if resp.error is not None:
                return [Finding(
                    source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                    source_url=SEARCH_URL, title="GitHub name search request failed", category=self.category,
                    metadata={"error": resp.error},
                )]
            if resp.status == 403:
                return [Finding(
                    source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                    source_url=SEARCH_URL, title="GitHub search API rate-limited", category=self.category,
                    metadata={"http_status": 403},
                )]
            if resp.status != 200:
                return []

            items = (resp.json() or {}).get("items") or []
            if items:
                break  # exact form matched -- no need to try the ASCII-folded variant

        return [
            Finding(
                source=self.name,
                identifier=identifier,
                status=MatchStatus.UNCERTAIN,
                source_url=item.get("html_url", ""),
                title=f"GitHub account with matching display name: {item.get('login')}",
                category=self.category,
                metadata={"login": item.get("login"), "avatar_url": item.get("avatar_url")},
                discovered_identifiers=[Identifier(value=item["login"], type=IdentifierType.USERNAME)],
                evidence_path=resp.evidence_path,
            )
            for item in items[:max_results]
            if item.get("login")
        ]
