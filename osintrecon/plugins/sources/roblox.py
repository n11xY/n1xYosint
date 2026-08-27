"""Roblox source module -- uses the public, unauthenticated Roblox users API
(https://users.roblox.com/v1/usernames/users) to confirm username existence.
Roblox doesn't expose a simple username-keyed profile URL that 404s the way
most sites do, so this needs its own small POST-based lookup rather than the
generic username_sites status/message check.
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://users.roblox.com/v1/usernames/users"


class RobloxPlugin(SourcePlugin):
    name: ClassVar[str] = "roblox"
    category: ClassVar[str] = "social"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    description: ClassVar[str] = "Looks up a username via the public Roblox users API."

    async def run(self, identifier: Identifier) -> list[Finding]:
        body = {"usernames": [identifier.value], "excludeBannedUsers": False}
        resp = await self.http.request(self.name, "POST", API_URL, json_body=body)

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=API_URL, title="Roblox API request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=API_URL, title=f"Roblox API returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        data = resp.json() or {}
        users = data.get("data") or []
        if not users:
            return []

        user = users[0]
        user_id = user.get("id")
        profile_url = f"https://www.roblox.com/users/{user_id}/profile" if user_id else API_URL
        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=profile_url,
            title=f"Roblox account: {user.get('name', identifier.value)}",
            category=self.category,
            metadata={
                "display_name": user.get("displayName"),
                "user_id": user_id,
                "api_url": API_URL,
            },
            evidence_path=resp.evidence_path,
        )]
