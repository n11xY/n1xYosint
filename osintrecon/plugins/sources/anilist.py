"""AniList source module -- uses AniList's public, unauthenticated GraphQL API
to confirm username existence and pull public profile metadata. No key
required. A CONFIRMED-quality alternative/complement to the HTML-heuristic
MyAnimeList check in the generic `username_sites` database.
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://graphql.anilist.co"

QUERY = """
query ($name: String) {
  User(name: $name) {
    id
    name
    siteUrl
    about
    createdAt
    avatar { large }
    statistics { anime { count } manga { count } }
  }
}
"""


class AniListPlugin(SourcePlugin):
    name: ClassVar[str] = "anilist"
    category: ClassVar[str] = "social"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    description: ClassVar[str] = "Looks up a username via the public AniList GraphQL API."

    async def run(self, identifier: Identifier) -> list[Finding]:
        body = {"query": QUERY, "variables": {"name": identifier.value}}
        resp = await self.http.request(
            self.name, "POST", API_URL, json_body=body,
            headers={"Accept": "application/json"}, expected_statuses={404},
        )

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=API_URL, title="AniList API request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status == 404:
            return []
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=API_URL, title=f"AniList API returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        data = (resp.json() or {}).get("data", {})
        user = data.get("User")
        if not user:
            return []

        stats = user.get("statistics") or {}
        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=user.get("siteUrl", API_URL),
            title=f"AniList account: {user.get('name', identifier.value)}",
            category=self.category,
            metadata={
                "user_id": user.get("id"),
                "about": user.get("about"),
                "created_at": user.get("createdAt"),
                "anime_count": (stats.get("anime") or {}).get("count"),
                "manga_count": (stats.get("manga") or {}).get("count"),
                "avatar": (user.get("avatar") or {}).get("large"),
            },
            evidence_path=resp.evidence_path,
        )]
