"""Steam source module -- uses the official Steam Web API
(ISteamUser/ResolveVanityURL) for a definitive (CONFIRMED) username lookup,
instead of the HTML-heuristic check in the generic `username_sites`
database. Requires a free Steam Web API key from
https://steamcommunity.com/dev/apikey.

Config:
  sources:
    steam_api:
      enabled: true
      api_key: "<your steam web api key>"
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

RESOLVE_URL = "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/"
SUMMARY_URL = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/"


class SteamAPIPlugin(SourcePlugin):
    name: ClassVar[str] = "steam_api"
    category: ClassVar[str] = "social"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    requires_api_key: ClassVar[bool] = True
    description: ClassVar[str] = "Looks up a Steam vanity username via the official Steam Web API."

    async def run(self, identifier: Identifier) -> list[Finding]:
        key = self.config["api_key"]
        resp = await self.http.get(self.name, RESOLVE_URL, params={"key": key, "vanityurl": identifier.value})

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=RESOLVE_URL, title="Steam API request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=RESOLVE_URL, title=f"Steam API returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        result = (resp.json() or {}).get("response", {})
        if result.get("success") != 1:
            return []

        steam_id = result.get("steamid")
        profile_url = f"https://steamcommunity.com/profiles/{steam_id}"
        metadata = {"steam_id": steam_id, "resolve_api_url": RESOLVE_URL}

        summary_resp = await self.http.get(self.name, SUMMARY_URL, params={"key": key, "steamids": steam_id})
        if summary_resp.status == 200:
            players = (summary_resp.json() or {}).get("response", {}).get("players", [])
            if players:
                player = players[0]
                metadata.update({
                    "persona_name": player.get("personaname"),
                    "profile_url": player.get("profileurl"),
                    "avatar": player.get("avatarfull"),
                    "account_created": player.get("timecreated"),
                    "country": player.get("loccountrycode"),
                })
                profile_url = player.get("profileurl", profile_url)

        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=profile_url,
            title=f"Steam account: {metadata.get('persona_name', identifier.value)}",
            category=self.category,
            metadata=metadata,
            evidence_path=resp.evidence_path,
        )]
