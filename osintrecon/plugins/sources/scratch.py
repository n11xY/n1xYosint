"""Scratch (MIT's block-based coding platform) source module -- uses the
official, free, keyless Scratch API (api.scratch.mit.edu) instead of the
HTML page, for a definitive existence check plus real profile metadata.

Live-verified: a real account (griffpatch) returns HTTP 200 with a full
profile object; a nonexistent one returns HTTP 404.
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://api.scratch.mit.edu/users/{}"


class ScratchPlugin(SourcePlugin):
    name: ClassVar[str] = "scratch"
    category: ClassVar[str] = "social"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    description: ClassVar[str] = "Looks up a username via Scratch's official, free, keyless API."

    async def run(self, identifier: Identifier) -> list[Finding]:
        url = API_URL.format(identifier.value)
        resp = await self.http.get(self.name, url, expected_statuses={404})

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title="Scratch API request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status == 404:
            return []
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title=f"Scratch API returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        data = resp.json() or {}
        profile = data.get("profile") or {}
        images = profile.get("images") or {}

        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=f"https://scratch.mit.edu/users/{identifier.value}/",
            title=f"Scratch account: {data.get('username', identifier.value)}",
            category=self.category,
            metadata={
                "joined": (data.get("history") or {}).get("joined"),
                "bio": profile.get("bio"),
                "status": profile.get("status"),
                "avatar_url": images.get("90x90"),
                "is_scratch_team": data.get("scratchteam"),
            },
            evidence_path=resp.evidence_path,
        )]
