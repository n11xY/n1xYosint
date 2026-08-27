"""Gravatar source module -- checks for a public Gravatar avatar/profile tied
to an email address using Gravatar's documented "d=404" avatar-existence
trick and the public profile JSON endpoint. No API key required.
"""
from __future__ import annotations

import hashlib
from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

AVATAR_URL = "https://www.gravatar.com/avatar/{}?d=404"
PROFILE_URL = "https://www.gravatar.com/{}.json"


def _email_hash(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def _legacy_md5(email: str) -> str:
    # Gravatar's avatar endpoint still keys off the legacy MD5 hash.
    return hashlib.md5(email.strip().lower().encode("utf-8")).hexdigest()  # noqa: S324 (not for security use)


class GravatarPlugin(SourcePlugin):
    name: ClassVar[str] = "gravatar"
    category: ClassVar[str] = "profile-directory"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.EMAIL}
    description: ClassVar[str] = "Checks for a public Gravatar avatar/profile associated with an email."

    async def run(self, identifier: Identifier) -> list[Finding]:
        h = _legacy_md5(identifier.value)
        avatar_url = AVATAR_URL.format(h)
        resp = await self.http.get(self.name, avatar_url, expected_statuses={404})

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=avatar_url, title="Gravatar request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status == 404:
            return []

        profile_url = PROFILE_URL.format(h)
        profile_resp = await self.http.get(self.name, profile_url, expected_statuses={404})
        metadata = {"avatar_url": f"https://www.gravatar.com/avatar/{h}"}
        title = f"Gravatar avatar exists for {identifier.value}"
        status = MatchStatus.PROBABLE

        if profile_resp.status == 200:
            data = profile_resp.json() or {}
            entries = data.get("entry", [])
            if entries:
                entry = entries[0]
                status = MatchStatus.CONFIRMED
                title = f"Gravatar profile: {entry.get('displayName', identifier.value)}"
                metadata.update({
                    "display_name": entry.get("displayName"),
                    "profile_url": entry.get("profileUrl"),
                    "about_me": entry.get("aboutMe"),
                    "location": entry.get("currentLocation"),
                    "accounts": [a.get("url") for a in entry.get("accounts", []) if a.get("url")],
                })

        return [Finding(
            source=self.name,
            identifier=identifier,
            status=status,
            source_url=metadata.get("profile_url", avatar_url),
            title=title,
            category=self.category,
            metadata=metadata,
            evidence_path=resp.evidence_path,
        )]
