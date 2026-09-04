"""YouTube Data API v3 source module -- uses the official, documented
`channels.list?forHandle=` endpoint to confirm a @handle resolves to a
real channel. Requires a free Google Cloud API key with the "YouTube Data
API v3" enabled (console.cloud.google.com -> APIs & Services -> Credentials;
free tier quota is generous for occasional lookups). Config:
sources.youtube_api.api_key.

The generic username_sites check for youtube.com/@{handle} (a plain
status-code check) was live-tested and found to work correctly in most
cases -- unlike Telegram/Threads, there's no fundamental reason it can't
work -- but this plugin exists alongside it anyway, for the same reason
twitch_api/steam_api exist alongside their own username_sites entries:
an official API result is a stronger, CONFIRMED-grade signal than a bare
HTTP status code, which can still occasionally be affected by bot
detection or A/B-tested page variants outside this project's control.

NOT live-verified by the maintainer: no Google Cloud API key was
available in this environment. `forHandle` is a real, documented
parameter (added when YouTube introduced @handles), but verify against a
real handle and a clearly nonexistent one before relying on this.
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://www.googleapis.com/youtube/v3/channels"


class YouTubeAPIPlugin(SourcePlugin):
    name: ClassVar[str] = "youtube_api"
    category: ClassVar[str] = "social"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    requires_api_key: ClassVar[bool] = True
    description: ClassVar[str] = (
        "Confirms a @handle resolves to a real channel via the official YouTube Data API v3 -- "
        "requires a free Google Cloud API key. NOT live-verified, see the module's docstring."
    )

    async def run(self, identifier: Identifier) -> list[Finding]:
        params = {
            "part": "snippet,statistics",
            "forHandle": identifier.value,
            "key": self.config["api_key"],
        }
        resp = await self.http.get(self.name, API_URL, params=params, expected_statuses={400, 403})

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=f"https://www.youtube.com/@{identifier.value}",
                title="YouTube Data API request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status == 403:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=f"https://www.youtube.com/@{identifier.value}",
                title="YouTube Data API quota exceeded or key invalid", category=self.category,
                metadata={"http_status": 403},
            )]
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=f"https://www.youtube.com/@{identifier.value}",
                title=f"YouTube Data API returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        items = (resp.json() or {}).get("items") or []
        if not items:
            return []

        channel = items[0]
        snippet = channel.get("snippet") or {}
        stats = channel.get("statistics") or {}

        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=f"https://www.youtube.com/@{identifier.value}",
            title=f"YouTube channel: {snippet.get('title', identifier.value)}",
            category=self.category,
            metadata={
                "channel_id": channel.get("id"),
                "description": snippet.get("description"),
                "published_at": snippet.get("publishedAt"),
                "country": snippet.get("country"),
                "subscriber_count": stats.get("subscriberCount"),
                "video_count": stats.get("videoCount"),
                "view_count": stats.get("viewCount"),
            },
            evidence_path=resp.evidence_path,
        )]
