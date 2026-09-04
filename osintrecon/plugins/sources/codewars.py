"""Codewars source module -- uses the official, free, keyless Codewars API
(codewars.com/api/v1) instead of the HTML page, for a definitive
existence check plus real profile metadata.

Live-verified: a real account (jhoffner) returns HTTP 200 with a profile
object; a nonexistent one returns HTTP 404.
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://www.codewars.com/api/v1/users/{}"


class CodewarsPlugin(SourcePlugin):
    name: ClassVar[str] = "codewars"
    category: ClassVar[str] = "code-hosting"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    description: ClassVar[str] = "Looks up a username via Codewars' official, free, keyless API."

    async def run(self, identifier: Identifier) -> list[Finding]:
        url = API_URL.format(identifier.value)
        resp = await self.http.get(self.name, url, expected_statuses={404})

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title="Codewars API request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status == 404:
            return []
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title=f"Codewars API returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        data = resp.json() or {}
        overall_rank = (data.get("ranks") or {}).get("overall") or {}

        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=f"https://www.codewars.com/users/{identifier.value}",
            title=f"Codewars account: {data.get('name') or identifier.value}",
            category=self.category,
            metadata={
                "name": data.get("name"),
                "honor": data.get("honor"),
                "clan": data.get("clan"),
                "leaderboard_position": data.get("leaderboardPosition"),
                "skills": data.get("skills"),
                "rank_name": overall_rank.get("name"),
                "rank_score": overall_rank.get("score"),
            },
            evidence_path=resp.evidence_path,
        )]
