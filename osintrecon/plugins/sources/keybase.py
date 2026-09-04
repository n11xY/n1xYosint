"""Keybase source module -- uses the official, free, keyless Keybase
lookup API (keybase.io/_/api) instead of the HTML page, for a definitive
existence check.

Live-verified: a real account (max) returns `status.code: 0` ("OK") with
a full profile object; a nonexistent one (properly formatted, not just
over Keybase's username length limit) returns `status.code: 205`
("NOT_FOUND") -- both with HTTP 200, so the body has to be checked, not
the status code.
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://keybase.io/_/api/1.0/user/lookup.json"


class KeybasePlugin(SourcePlugin):
    name: ClassVar[str] = "keybase"
    category: ClassVar[str] = "profile-directory"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    description: ClassVar[str] = "Looks up a username via Keybase's official, free, keyless API."

    async def run(self, identifier: Identifier) -> list[Finding]:
        resp = await self.http.get(self.name, API_URL, params={"username": identifier.value})

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=API_URL, title="Keybase API request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=API_URL, title=f"Keybase API returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        data = resp.json() or {}
        if (data.get("status") or {}).get("code") != 0:
            return []  # includes NOT_FOUND (205) and INPUT_ERROR (100) alike -- no match either way

        them = data.get("them") or {}
        basics = them.get("basics") or {}
        profile = them.get("profile") or {}

        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=f"https://keybase.io/{identifier.value}",
            title=f"Keybase account: {basics.get('username', identifier.value)}",
            category=self.category,
            metadata={
                "full_name": profile.get("full_name"),
                "bio": profile.get("bio"),
                "location": profile.get("location"),
                "joined": basics.get("ctime"),
            },
            evidence_path=resp.evidence_path,
        )]
