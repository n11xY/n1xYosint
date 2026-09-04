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
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

SEARCH_URL = "https://api.github.com/search/users"
MAX_RESULTS = 5


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
        params = {"q": f'"{identifier.value}" in:name', "per_page": MAX_RESULTS}

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
            for item in items[:MAX_RESULTS]
            if item.get("login")
        ]
