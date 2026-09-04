"""Docker Hub source module -- uses the official, free, keyless Docker Hub
API (hub.docker.com/v2) instead of the HTML page, for a definitive
existence check plus real profile metadata.

Live-verified: a real account (docker) returns HTTP 200 (after a 308
redirect the HTTP client already follows) with a full profile object; a
nonexistent one returns HTTP 404.
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://hub.docker.com/v2/users/{}/"


class DockerHubPlugin(SourcePlugin):
    name: ClassVar[str] = "dockerhub"
    category: ClassVar[str] = "code-hosting"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    description: ClassVar[str] = "Looks up a username via Docker Hub's official, free, keyless API."

    async def run(self, identifier: Identifier) -> list[Finding]:
        url = API_URL.format(identifier.value)
        resp = await self.http.get(self.name, url, expected_statuses={404})

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title="Docker Hub API request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status == 404:
            return []
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title=f"Docker Hub API returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        data = resp.json() or {}
        display_name = data.get("full_name") or data.get("username") or identifier.value

        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=f"https://hub.docker.com/u/{identifier.value}",
            title=f"Docker Hub account: {display_name}",
            category=self.category,
            metadata={
                "type": data.get("type"),
                "company": data.get("company"),
                "location": data.get("location"),
                "profile_url": data.get("profile_url"),
                "date_joined": data.get("date_joined"),
                "avatar_url": data.get("gravatar_url"),
            },
            evidence_path=resp.evidence_path,
        )]
