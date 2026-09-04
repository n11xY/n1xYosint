"""DEV.to source module -- uses the official, free, keyless DEV Community
API (developers.forem.com/api) instead of the HTML page, for a definitive
existence check plus real profile metadata.

Live-verified: a real account (ben) returns HTTP 200 with a full profile
object; a nonexistent one returns HTTP 404 with a JSON error body.
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://dev.to/api/users/by_username"


class DevToPlugin(SourcePlugin):
    name: ClassVar[str] = "devto"
    category: ClassVar[str] = "forum"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    description: ClassVar[str] = "Looks up a username via DEV.to's official, free, keyless API."

    async def run(self, identifier: Identifier) -> list[Finding]:
        resp = await self.http.get(self.name, API_URL, params={"url": identifier.value}, expected_statuses={404})

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=API_URL, title="DEV.to API request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status == 404:
            return []
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=API_URL, title=f"DEV.to API returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        data = resp.json() or {}
        # DEV.to profiles often link a GitHub/Twitter username directly --
        # feeds --depth enrichment the same way github.py's discovered
        # email/blog fields do.
        discovered: list[Identifier] = []
        if data.get("github_username"):
            discovered.append(Identifier(value=data["github_username"], type=IdentifierType.USERNAME))
        if data.get("twitter_username"):
            discovered.append(Identifier(value=data["twitter_username"], type=IdentifierType.USERNAME))

        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=f"https://dev.to/{identifier.value}",
            title=f"DEV.to account: {data.get('name', identifier.value)}",
            category=self.category,
            metadata={
                "name": data.get("name"),
                "summary": data.get("summary"),
                "location": data.get("location"),
                "joined_at": data.get("joined_at"),
                "avatar_url": data.get("profile_image"),
                "twitter_username": data.get("twitter_username"),
                "github_username": data.get("github_username"),
                "website_url": data.get("website_url"),
            },
            discovered_identifiers=discovered,
            evidence_path=resp.evidence_path,
        )]
