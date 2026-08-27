"""GitLab source module -- uses the public, unauthenticated GitLab REST API
(https://gitlab.com/api/v4/users) to confirm account existence and pull
public profile metadata."""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://gitlab.com/api/v4/users?username={}"


class GitLabPlugin(SourcePlugin):
    name: ClassVar[str] = "gitlab"
    category: ClassVar[str] = "code-hosting"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    description: ClassVar[str] = "Looks up a username via the public GitLab REST API."

    async def run(self, identifier: Identifier) -> list[Finding]:
        url = API_URL.format(identifier.value)
        resp = await self.http.get(self.name, url)

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title="GitLab API request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title=f"GitLab API returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        results = resp.json() or []
        matches = [u for u in results if u.get("username", "").lower() == identifier.value.lower()]
        if not matches:
            return []

        user = matches[0]
        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=user.get("web_url", f"https://gitlab.com/{identifier.value}"),
            title=f"GitLab account: {user.get('username', identifier.value)}",
            category=self.category,
            metadata={
                "name": user.get("name"),
                "state": user.get("state"),
                "avatar_url": user.get("avatar_url"),
                "id": user.get("id"),
                "api_url": url,
            },
            evidence_path=resp.evidence_path,
        )]
