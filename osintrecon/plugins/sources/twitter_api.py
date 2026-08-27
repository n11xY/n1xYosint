"""X/Twitter source module -- uses the official X API v2
(GET /2/users/by/username/:username) for a definitive (CONFIRMED) lookup.
Deliberately NOT included as a `username_sites` heuristic entry: x.com/
twitter.com serve the same HTTP 200 response whether or not a handle
exists, so an unauthenticated HTML check there cannot distinguish a real
match from a false one. The official API can, but requires a developer
account and Bearer token (https://developer.x.com), so this module is
disabled by default.

Config:
  sources:
    twitter_api:
      enabled: true
      bearer_token: "<your app-only bearer token>"
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://api.twitter.com/2/users/by/username/{}"


class TwitterAPIPlugin(SourcePlugin):
    name: ClassVar[str] = "twitter_api"
    category: ClassVar[str] = "social"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    requires_api_key: ClassVar[bool] = True
    description: ClassVar[str] = "Looks up a username via the official X (Twitter) API v2. Requires a developer Bearer token."

    def is_configured(self) -> bool:
        return bool(self.config.get("bearer_token"))

    async def run(self, identifier: Identifier) -> list[Finding]:
        url = API_URL.format(identifier.value)
        headers = {"Authorization": f"Bearer {self.config['bearer_token']}"}
        params = {"user.fields": "created_at,description,location,public_metrics,profile_image_url"}
        resp = await self.http.get(self.name, url, headers=headers, params=params, expected_statuses={404})

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title="X API request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status not in (200, 404):
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title=f"X API returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        data = resp.json() or {}
        user = data.get("data")
        if not user:
            return []

        metrics = user.get("public_metrics", {})
        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=f"https://x.com/{user.get('username', identifier.value)}",
            title=f"X/Twitter account: {user.get('name', identifier.value)}",
            category=self.category,
            metadata={
                "display_name": user.get("name"),
                "description": user.get("description"),
                "location": user.get("location"),
                "created_at": user.get("created_at"),
                "followers_count": metrics.get("followers_count"),
                "following_count": metrics.get("following_count"),
                "tweet_count": metrics.get("tweet_count"),
                "profile_image_url": user.get("profile_image_url"),
            },
            evidence_path=resp.evidence_path,
        )]
