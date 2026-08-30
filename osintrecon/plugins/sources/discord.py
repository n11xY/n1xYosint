"""Discord source module -- Discord has no public profile pages, so the
usual "does this page 404" approach is impossible there. Instead this uses
Discord's own username-availability check, the same endpoint their signup
form calls, which returns whether a username is already taken without
requiring login. Live-verified against both taken and available usernames
before shipping.
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://discord.com/api/v9/unique-username/username-attempt-unauthed"


class DiscordPlugin(SourcePlugin):
    name: ClassVar[str] = "discord"
    category: ClassVar[str] = "social"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    description: ClassVar[str] = "Checks Discord's own username-availability endpoint (no public profiles exist to scrape)."

    async def run(self, identifier: Identifier) -> list[Finding]:
        resp = await self.http.request(
            self.name, "POST", API_URL,
            json_body={"username": identifier.value},
            headers={"Content-Type": "application/json"},
        )

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=API_URL, title="Discord API request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status != 200:
            # Discord rejects reserved words, invalid formats, and rate-limits
            # this endpoint with non-200 responses that carry no reliable
            # taken/available signal -- report as inconclusive, not a match.
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=API_URL, title=f"Discord API returned {resp.status} (invalid/reserved name or rate-limited)",
                category=self.category, metadata={"http_status": resp.status},
            )]

        data = resp.json() or {}
        if "taken" not in data:
            return []

        if not data["taken"]:
            return []  # available -- no such account

        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=API_URL,
            title=f"Discord username taken: {identifier.value}",
            category=self.category,
            metadata={
                "note": "Discord has no public profile pages; this only confirms the "
                        "username is registered, not which account owns it.",
            },
            evidence_path=resp.evidence_path,
        )]
