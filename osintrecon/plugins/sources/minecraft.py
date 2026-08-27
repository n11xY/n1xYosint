"""Minecraft (Mojang) source module -- uses the public, unauthenticated Mojang
API to resolve a Minecraft username to its account UUID. Returns HTTP 200
with a JSON body when the username exists. The "not found" status has varied
across API revisions (204 No Content historically, 404 Not Found observed in
practice) so both are treated as a clean not-found result -- no API key
required.
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://api.mojang.com/users/profiles/minecraft/{}"


class MinecraftPlugin(SourcePlugin):
    name: ClassVar[str] = "minecraft"
    category: ClassVar[str] = "social"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    description: ClassVar[str] = "Looks up a username via the public Mojang (Minecraft) API."

    async def run(self, identifier: Identifier) -> list[Finding]:
        url = API_URL.format(identifier.value)
        resp = await self.http.get(self.name, url, expected_statuses={204, 404})

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title="Mojang API request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status in (204, 404):
            return []
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title=f"Mojang API returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        data = resp.json() or {}
        uuid = data.get("id")
        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=f"https://namemc.com/profile/{data.get('name', identifier.value)}",
            title=f"Minecraft account: {data.get('name', identifier.value)}",
            category=self.category,
            metadata={"uuid": uuid, "api_url": url},
            evidence_path=resp.evidence_path,
        )]
