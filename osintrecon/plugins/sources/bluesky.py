"""Bluesky source module -- uses the public, unauthenticated AT Protocol API
(https://public.api.bsky.app) to confirm account existence. No key needed;
most read endpoints on the public AppView are open.

Checks the default free handle format (`{username}.bsky.social`), since a
bare username isn't itself a valid Bluesky handle -- accounts without a
custom domain get this suffix automatically at signup.
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile"


class BlueskyPlugin(SourcePlugin):
    name: ClassVar[str] = "bluesky"
    category: ClassVar[str] = "social"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    description: ClassVar[str] = "Looks up a username via the public Bluesky (AT Protocol) API."

    async def run(self, identifier: Identifier) -> list[Finding]:
        handle = f"{identifier.value}.bsky.social"
        resp = await self.http.get(self.name, API_URL, params={"actor": handle}, expected_statuses={400, 404})

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=API_URL, title="Bluesky API request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status in (400, 404):
            return []  # AT Protocol XRPC returns 400 with an error body for an unknown handle
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=API_URL, title=f"Bluesky API returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        data = resp.json() or {}
        if not data.get("did"):
            return []

        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=f"https://bsky.app/profile/{handle}",
            title=f"Bluesky account: {data.get('displayName') or handle}",
            category=self.category,
            metadata={
                "handle": data.get("handle"),
                "did": data.get("did"),
                "display_name": data.get("displayName"),
                "description": data.get("description"),
                "followers_count": data.get("followersCount"),
                "follows_count": data.get("followsCount"),
                "posts_count": data.get("postsCount"),
                "created_at": data.get("createdAt"),
            },
            evidence_path=resp.evidence_path,
        )]
