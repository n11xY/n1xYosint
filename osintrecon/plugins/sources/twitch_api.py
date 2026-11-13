"""Twitch source module: a definitive (CONFIRMED) username lookup with the
account's facts -- sign-up date, followers, profile picture.

Two ways in, the first that applies:

1. **Your own Twitch application** (Client ID + Client Secret, registered at
   https://dev.twitch.tv/console/apps): the official Helix API, app-level
   client-credentials auth -- it never asks for or uses anyone's login.
2. **Without one**: decapi.me, a public, keyless service that answers from
   Twitch's Helix API. twitch.tv itself no longer serves small channels'
   pages to anonymous requests (a five-year-old account got the same
   generic page as a made-up name), so the page can't be read instead.
   Live-verified 2026-11-13: "shroud" -> created Nov 3 2012, 11.3M
   followers, an avatar URL; a made-up name -> HTTP 400 "Invalid Twitch
   username specified".

Config (optional; without it the keyless route is used):
  sources:
    twitch_api:
      client_id: "<your client id>"
      client_secret: "<your client secret>"
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone
from typing import ClassVar, Optional
from urllib.parse import quote

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

TOKEN_URL = "https://id.twitch.tv/oauth2/token"
USERS_URL = "https://api.twitch.tv/helix/users"
DECAPI = "https://decapi.me/twitch/{what}/{login}"
PROFILE_URL = "https://www.twitch.tv/{}"
# Twitch logins: 4-25 letters, digits or underscores. Anything else can't be
# an account, so it isn't asked about.
LOGIN = re.compile(r"^[A-Za-z0-9_]{4,25}$")


def _decapi_date(text: str) -> Optional[str]:
    """decapi's "Nov 3. 2012 - 03:50:32 PM (UTC)" -> ISO 8601, or None."""
    try:
        moment = datetime.strptime(text.strip(), "%b %d. %Y - %I:%M:%S %p (UTC)")
    except ValueError:
        return None
    return moment.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


class TwitchAPIPlugin(SourcePlugin):
    name: ClassVar[str] = "twitch_api"
    category: ClassVar[str] = "social"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    description: ClassVar[str] = ("Looks up a username on Twitch: the official Helix API with your own app, "
                                  "otherwise a keyless Helix-backed lookup.")

    def __init__(self, config: dict, http):
        super().__init__(config, http)
        self._token: Optional[str] = None
        self._token_expiry: float = 0.0
        self._token_lock = asyncio.Lock()

    def _has_app(self) -> bool:
        return bool(self.config.get("client_id")) and bool(self.config.get("client_secret"))

    async def run(self, identifier: Identifier) -> list[Finding]:
        if not LOGIN.match(identifier.value):
            return []
        if self._has_app():
            return await self._run_helix(identifier)
        return await self._run_keyless(identifier)

    # -- without an app: decapi.me ------------------------------------------

    async def _run_keyless(self, identifier: Identifier) -> list[Finding]:
        login = quote(identifier.value, safe="")
        created_url = DECAPI.format(what="creation", login=login)
        resp = await self.http.get(self.name, created_url, expected_statuses={400, 404})
        text = (resp.text or "").strip()
        if resp.error is not None or resp.status >= 500 or resp.status == 429:
            return [self._error(identifier, created_url, resp.error or f"HTTP {resp.status}")]
        created = _decapi_date(text) if resp.status == 200 else None
        if created is None:
            if resp.status in (400, 404) or re.search(r"invalid|not found|does not exist", text, re.IGNORECASE):
                return []
            # An answer that is neither a date nor "no such user" is decapi
            # saying something unexpected -- not evidence either way.
            return [self._error(identifier, created_url, f"unexpected answer: {text[:120]}")]

        followers_resp, avatar_resp = await asyncio.gather(
            self.http.get(self.name, DECAPI.format(what="followcount", login=login)),
            self.http.get(self.name, DECAPI.format(what="avatar", login=login)),
        )
        followers = (followers_resp.text or "").strip() if followers_resp.status == 200 else ""
        avatar = (avatar_resp.text or "").strip() if avatar_resp.status == 200 else ""
        return [Finding(
            source=self.name, identifier=identifier, status=MatchStatus.CONFIRMED,
            source_url=PROFILE_URL.format(identifier.value.lower()),
            title=f"Twitch account: {identifier.value}", category=self.category,
            metadata={
                "created_at": created,
                "followers": int(followers) if followers.isdigit() else None,
                "profile_image_url": avatar if avatar.startswith("https://") else None,
                "data_origin": "decapi.me (Twitch Helix)",
            },
        )]

    # -- with an app: the official Helix API --------------------------------

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

    async def _run_helix(self, identifier: Identifier) -> list[Finding]:
        token = await self._get_token()
        if token is None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=TOKEN_URL, title="Twitch OAuth token request failed", category=self.category,
            )]

        headers = {"Client-Id": self.config["client_id"], "Authorization": f"Bearer {token}"}
        resp = await self.http.get(self.name, USERS_URL, headers=headers, params={"login": identifier.value})

        if resp.error is not None:
            return [self._error(identifier, USERS_URL, resp.error)]
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
            source_url=PROFILE_URL.format(user.get("login", identifier.value)),
            title=f"Twitch account: {user.get('display_name', identifier.value)}",
            category=self.category,
            metadata={
                "display_name": user.get("display_name"),
                "description": user.get("description"),
                "broadcaster_type": user.get("broadcaster_type"),
                "created_at": user.get("created_at"),
                "profile_image_url": user.get("profile_image_url"),
                "view_count": user.get("view_count"),
                "banner_url": user.get("offline_image_url") or None,
            },
            evidence_path=resp.evidence_path,
        )]

    def _error(self, identifier: Identifier, url: str, error: str) -> Finding:
        return Finding(
            source=self.name, identifier=identifier, status=MatchStatus.ERROR,
            source_url=url, title="Twitch lookup failed", category=self.category,
            metadata={"error": error},
        )
