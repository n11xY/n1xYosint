"""Speedrun.com source module -- uses the official, free, keyless
speedrun.com API (speedrun.com/api/v1) instead of the HTML page, for a
definitive existence check plus real profile metadata.

Live-verified: a real account (cheese05, via `?lookup=`, an exact-name
match) returns HTTP 200 with a `data` array containing one user object;
a nonexistent username returns HTTP 200 with an empty `data` array (not
a 404 -- has to be checked as a body value).
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://www.speedrun.com/api/v1/users"


def _linked_username(link: dict | None) -> str:
    if not link:
        return ""
    uri = link.get("uri", "") or ""
    return uri.rstrip("/").rsplit("/", 1)[-1]


class SpeedrunComPlugin(SourcePlugin):
    name: ClassVar[str] = "speedrun_com"
    category: ClassVar[str] = "social"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    description: ClassVar[str] = "Looks up a username via speedrun.com's official, free, keyless API."

    async def run(self, identifier: Identifier) -> list[Finding]:
        resp = await self.http.get(self.name, API_URL, params={"lookup": identifier.value})

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=API_URL, title="speedrun.com API request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=API_URL, title=f"speedrun.com API returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        users = (resp.json() or {}).get("data") or []
        if not users:
            return []

        user = users[0]
        location = (user.get("location") or {}).get("country") or {}

        discovered: list[Identifier] = []
        twitch_username = _linked_username(user.get("twitch"))
        youtube_username = _linked_username(user.get("youtube"))
        if twitch_username:
            discovered.append(Identifier(value=twitch_username, type=IdentifierType.USERNAME))
        if youtube_username:
            discovered.append(Identifier(value=youtube_username, type=IdentifierType.USERNAME))

        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=user.get("weblink", f"https://www.speedrun.com/users/{identifier.value}"),
            title=f"speedrun.com account: {(user.get('names') or {}).get('international', identifier.value)}",
            category=self.category,
            metadata={
                "signup": user.get("signup"),
                "country": location.get("code"),
                "twitch_url": (user.get("twitch") or {}).get("uri"),
                "youtube_url": (user.get("youtube") or {}).get("uri"),
                "twitter_url": (user.get("twitter") or {}).get("uri"),
            },
            discovered_identifiers=discovered,
            evidence_path=resp.evidence_path,
        )]
