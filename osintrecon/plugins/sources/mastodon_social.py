"""Mastodon (mastodon.social) source module -- uses that instance's
official, free, keyless public API (`/api/v1/accounts/lookup`) instead of
the HTML page, for a definitive existence check plus real profile
metadata. Scoped to mastodon.social specifically, matching the existing
`username_sites` entry for it -- Mastodon is federated, so this can't
generalize to "any Mastodon instance" the way most other plugins here
check one fixed platform.

Live-verified: a real account (Gargron, Mastodon's creator) returns HTTP
200 with a full profile object; a nonexistent one returns HTTP 404.
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://mastodon.social/api/v1/accounts/lookup"


class MastodonSocialPlugin(SourcePlugin):
    name: ClassVar[str] = "mastodon_social"
    category: ClassVar[str] = "social"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    description: ClassVar[str] = "Looks up a username on mastodon.social via its official, free, keyless API."

    async def run(self, identifier: Identifier) -> list[Finding]:
        resp = await self.http.get(
            self.name, API_URL, params={"acct": identifier.value}, expected_statuses={404},
        )

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=API_URL, title="Mastodon API request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status == 404:
            return []
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=API_URL, title=f"Mastodon API returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        data = resp.json() or {}
        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=data.get("url", f"https://mastodon.social/@{identifier.value}"),
            title=f"Mastodon (mastodon.social) account: {data.get('display_name') or identifier.value}",
            category=self.category,
            metadata={
                "display_name": data.get("display_name"),
                "created_at": data.get("created_at"),
                "followers_count": data.get("followers_count"),
                "statuses_count": data.get("statuses_count"),
                "bot": data.get("bot"),
                "avatar_url": data.get("avatar"),
            },
            evidence_path=resp.evidence_path,
        )]
