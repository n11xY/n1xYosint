"""Twitch source module -- uses the official Twitch Helix API for a
definitive (CONFIRMED) username lookup, instead of the HTML-heuristic check
in the generic `username_sites` database.

Requires a free Twitch application (Client ID + Client Secret, registered at
https://dev.twitch.tv/console/apps). This is app-level, client-credentials
auth -- it never asks for or uses a user's personal Twitch login.

Config:
  sources:
    twitch_api:
      enabled: true
      client_id: "<your client id>"
      client_secret: "<your client secret>"
"""
from __future__ import annotations

import asyncio
import time
from typing import ClassVar, Optional

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

TOKEN_URL = "https://id.twitch.tv/oauth2/token"
USERS_URL = "https://api.twitch.tv/helix/users"


class TwitchAPIPlugin(SourcePlugin):
    name: ClassVar[str] = "twitch_api"
    category: ClassVar[str] = "social"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    requires_api_key: ClassVar[bool] = True  # client_id + client_secret; see is_configured() override
    description: ClassVar[str] = "Looks up a username via the official Twitch Helix API (app-only auth)."

    def __init__(self, config: dict, http):
        super().__init__(config, http)
        self._token: Optional[str] = None
        self._token_expiry: float = 0.0
        self._token_lock = asyncio.Lock()

    def is_configured(self) -> bool:
        return bool(self.config.get("client_id")) and bool(self.config.get("client_secret"))

    async def _get_token(self) -> Optional[str]:
        async with self._token_lock:
            if self._token and time.time() < self._token_expiry - 60:
                return self._token
            resp = await self.http.request(
                self.name, "POST", TOKEN_URL,
                params={
                    "client_id": self.config["client_id"],
                    "client_secret": self.config["client_secret"],
                    "grant_type": "client_credentials",
                },
                allow_cache=False,
            )
            if resp.error is not None or resp.status != 200:
                return None
            data = resp.json() or {}
            self._token = data.get("access_token")
            self._token_expiry = time.time() + float(data.get("expires_in", 3600))
            return self._token

    async def run(self, identifier: Identifier) -> list[Finding]:
        token = await self._get_token()
        if token is None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=TOKEN_URL, title="Twitch OAuth token request failed", category=self.category,
            )]

        headers = {"Client-Id": self.config["client_id"], "Authorization": f"Bearer {token}"}
        resp = await self.http.get(self.name, USERS_URL, headers=headers, params={"login": identifier.value})

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=USERS_URL, title="Twitch API request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=USERS_URL, title=f"Twitch API returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        users = (resp.json() or {}).get("data", [])
        if not users:
            return []

        user = users[0]
        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=f"https://www.twitch.tv/{user.get('login', identifier.value)}",
            title=f"Twitch account: {user.get('display_name', identifier.value)}",
            category=self.category,
            metadata={
                "display_name": user.get("display_name"),
                "description": user.get("description"),
                "broadcaster_type": user.get("broadcaster_type"),
                "created_at": user.get("created_at"),
                "profile_image_url": user.get("profile_image_url"),
                "view_count": user.get("view_count"),
            },
            evidence_path=resp.evidence_path,
        )]
