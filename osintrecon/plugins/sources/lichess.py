"""Lichess source module -- uses the official, free, keyless Lichess API
(lichess.org/api) instead of the HTML page, for a definitive existence
check plus real profile/rating metadata.

Live-verified: a real account (DrNykterstein) returns HTTP 200 with a
profile object; a nonexistent one returns HTTP 404.
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://lichess.org/api/user/{}"


class LichessPlugin(SourcePlugin):
    name: ClassVar[str] = "lichess"
    category: ClassVar[str] = "social"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    description: ClassVar[str] = "Looks up a username via Lichess' official, free, keyless API."

    async def run(self, identifier: Identifier) -> list[Finding]:
        url = API_URL.format(identifier.value)
        resp = await self.http.get(self.name, url, expected_statuses={404})

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title="Lichess API request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status == 404:
            return []
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title=f"Lichess API returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        data = resp.json() or {}
        profile = data.get("profile") or {}
        perfs = data.get("perfs") or {}
        blitz = perfs.get("blitz") or {}

        metadata = {
            "title": data.get("title"),
            "real_name": profile.get("realName") or " ".join(
                filter(None, [profile.get("firstName"), profile.get("lastName")])
            ) or None,
            "country": profile.get("flag"),
            "location": profile.get("location"),
            "bio": profile.get("bio"),
            "created_at": data.get("createdAt"),
            "seen_at": data.get("seenAt"),
            "blitz_rating": blitz.get("rating"),
        }

        # A linked Twitch channel, when set -- feeds --depth enrichment the
        # same way github.py's discovered email/blog fields do.
        discovered: list[Identifier] = []
        twitch_channel = ((data.get("streamer") or {}).get("twitch") or {}).get("channel", "")
        twitch_username = twitch_channel.rstrip("/").rsplit("/", 1)[-1]
        if twitch_username:
            metadata["twitch_url"] = twitch_channel
            discovered.append(Identifier(value=twitch_username, type=IdentifierType.USERNAME))

        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=data.get("url", f"https://lichess.org/@/{identifier.value}"),
            title=f"Lichess account: {data.get('username', identifier.value)}",
            category=self.category,
            metadata=metadata,
            discovered_identifiers=discovered,
            evidence_path=resp.evidence_path,
        )]
