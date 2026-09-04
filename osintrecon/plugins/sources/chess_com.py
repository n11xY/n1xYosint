"""Chess.com source module -- uses the official, free, keyless Chess.com
Published-Data API (published-data.chess.com) instead of the HTML page,
for a definitive existence check plus real profile/stats metadata.

Live-verified: a real account (hikaru) returns HTTP 200 with a profile
object; a nonexistent one returns HTTP 404.
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

PROFILE_URL = "https://api.chess.com/pub/player/{}"
STATS_URL = "https://api.chess.com/pub/player/{}/stats"


class ChessComPlugin(SourcePlugin):
    name: ClassVar[str] = "chess_com"
    category: ClassVar[str] = "social"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    description: ClassVar[str] = "Looks up a username via Chess.com's official, free, keyless Published-Data API."

    async def run(self, identifier: Identifier) -> list[Finding]:
        url = PROFILE_URL.format(identifier.value)
        resp = await self.http.get(self.name, url, expected_statuses={404})

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title="Chess.com API request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status == 404:
            return []
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title=f"Chess.com API returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        data = resp.json() or {}
        metadata = {
            "name": data.get("name"),
            "title": data.get("title"),
            "country": data.get("country"),
            "location": data.get("location"),
            "avatar_url": data.get("avatar"),
            "followers": data.get("followers"),
            "joined": data.get("joined"),
            "last_online": data.get("last_online"),
            "status": data.get("status"),
        }

        stats_resp = await self.http.get(self.name, STATS_URL.format(identifier.value), expected_statuses={404})
        if stats_resp.status == 200:
            stats = stats_resp.json() or {}
            rapid = stats.get("chess_rapid", {}).get("last", {})
            blitz = stats.get("chess_blitz", {}).get("last", {})
            metadata.update({
                "rapid_rating": rapid.get("rating"),
                "blitz_rating": blitz.get("rating"),
            })

        # Chess.com surfaces a linked Twitch channel for streamers --
        # feeds --depth enrichment the same way github.py's discovered
        # email/blog fields do.
        discovered: list[Identifier] = []
        twitch_url = data.get("twitch_url", "")
        if twitch_url.rstrip("/").rsplit("/", 1)[-1]:
            metadata["twitch_url"] = twitch_url
            discovered.append(Identifier(value=twitch_url.rstrip("/").rsplit("/", 1)[-1], type=IdentifierType.USERNAME))

        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=data.get("url", f"https://www.chess.com/member/{identifier.value}"),
            title=f"Chess.com account: {metadata.get('name') or identifier.value}",
            category=self.category,
            metadata=metadata,
            discovered_identifiers=discovered,
            evidence_path=resp.evidence_path,
        )]
