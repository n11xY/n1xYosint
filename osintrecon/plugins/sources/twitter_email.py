"""X/Twitter email-registration check -- uses the email-availability
endpoint X's own web signup form calls to tell whether an email is already
attached to an account, without needing a developer API key. Distinct from
twitter_api.py (which looks up a *username* via the official, key-gated API
v2): this checks an *email* via an unofficial-but-public endpoint, live-
verified against both a taken and an available address before shipping.
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://api.twitter.com/i/users/email_available.json"


class TwitterEmailPlugin(SourcePlugin):
    name: ClassVar[str] = "twitter_email"
    category: ClassVar[str] = "social"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.EMAIL}
    description: ClassVar[str] = "Checks X/Twitter's own signup-flow endpoint for email registration."

    async def run(self, identifier: Identifier) -> list[Finding]:
        resp = await self.http.get(self.name, API_URL, params={"email": identifier.value})

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=API_URL, title="X email-check request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=API_URL, title=f"X email-check returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        data = resp.json() or {}
        if "taken" not in data:
            return []
        if not data["taken"]:
            return []  # available -- no account uses this email

        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=API_URL,
            title="Email registered on X/Twitter",
            category=self.category,
            metadata={"note": "Confirms the email is attached to some account; does not reveal the handle."},
            evidence_path=resp.evidence_path,
        )]
