"""Nexus Mods source module -- uses Nexus Mods' public GraphQL API (v2,
api.nexusmods.com/v2/graphql, no key needed) for a definitive username
lookup. The profile pages sit behind a Cloudflare challenge that answers
real and made-up names alike, so they can't be read; the API can.

Live-verified 2026-11-13: "Dark0ne" (the site's founder, member 1) returns
a user object with the join date and avatar; a made-up name returns
`"userByName": null`.
"""
from __future__ import annotations

from typing import ClassVar
from urllib.parse import quote

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://api.nexusmods.com/v2/graphql"
PROFILE_URL = "https://next.nexusmods.com/profile/{}"
QUERY = "query($name: String!) { userByName(name: $name) { memberId name joined avatar } }"


class NexusModsPlugin(SourcePlugin):
    name: ClassVar[str] = "nexusmods"
    category: ClassVar[str] = "social"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    description: ClassVar[str] = "Looks up a username via Nexus Mods' public, keyless GraphQL API."

    async def run(self, identifier: Identifier) -> list[Finding]:
        resp = await self.http.request(self.name, "POST", API_URL,
                                       json_body={"query": QUERY, "variables": {"name": identifier.value}})
        if resp.error is not None or resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=API_URL, category=self.category,
                title="Nexus Mods API request failed" if resp.error is not None
                else f"Nexus Mods API returned {resp.status}",
                metadata={"error": resp.error} if resp.error is not None else {"http_status": resp.status},
            )]

        payload = resp.json() or {}
        user = (payload.get("data") or {}).get("userByName")
        if payload.get("errors") and user is None:
            # A GraphQL error is the API failing to answer, not "no such user".
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=API_URL, category=self.category, title="Nexus Mods API returned an error",
                metadata={"error": str(payload["errors"])[:300]},
            )]
        if not user:
            return []

        name = user.get("name") or identifier.value
        return [Finding(
            source=self.name, identifier=identifier, status=MatchStatus.CONFIRMED,
            source_url=PROFILE_URL.format(quote(name, safe="")),
            title=f"Nexus Mods account: {name}", category=self.category,
            metadata={
                "member_id": user.get("memberId"),
                "joined": user.get("joined"),
                "avatar_url": user.get("avatar"),
                "api": API_URL,
            },
        )]
