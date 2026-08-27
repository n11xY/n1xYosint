"""GitHub source module -- uses the public, unauthenticated GitHub REST API
(https://api.github.com) to confirm account existence and pull public profile
metadata. An optional personal access token (config: sources.github.api_key)
raises the API rate limit but is not required.
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://api.github.com/users/{}"


class GitHubPlugin(SourcePlugin):
    name: ClassVar[str] = "github"
    category: ClassVar[str] = "code-hosting"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    description: ClassVar[str] = "Looks up a username via the public GitHub REST API."

    async def run(self, identifier: Identifier) -> list[Finding]:
        url = API_URL.format(identifier.value)
        headers = {"Accept": "application/vnd.github+json"}
        token = self.config.get("api_key")
        if token:
            headers["Authorization"] = f"Bearer {token}"

        resp = await self.http.get(self.name, url, headers=headers, expected_statuses={404, 403})

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title="GitHub API request failed", category=self.category,
                metadata={"error": resp.error},
            )]

        if resp.status == 404:
            return []
        if resp.status == 403:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title="GitHub API rate-limited", category=self.category,
                metadata={"http_status": 403},
            )]
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title=f"GitHub API returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        data = resp.json() or {}
        discovered: list[Identifier] = []
        blog = data.get("blog") or ""
        if blog:
            discovered.append(Identifier(value=blog, type=IdentifierType.URL))
        email = data.get("email")
        if email:
            discovered.append(Identifier(value=email.lower(), type=IdentifierType.EMAIL))

        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=f"https://github.com/{identifier.value}",
            title=f"GitHub account: {data.get('login', identifier.value)}",
            category=self.category,
            metadata={
                "name": data.get("name"),
                "bio": data.get("bio"),
                "company": data.get("company"),
                "location": data.get("location"),
                "public_repos": data.get("public_repos"),
                "followers": data.get("followers"),
                "created_at": data.get("created_at"),
                "avatar_url": data.get("avatar_url"),
                "blog": blog,
                "api_url": url,
            },
            discovered_identifiers=discovered,
            evidence_path=resp.evidence_path,
        )]
