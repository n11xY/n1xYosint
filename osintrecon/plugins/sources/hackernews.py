"""Hacker News source module -- uses Y Combinator's official, free, keyless
Firebase-backed API (hacker-news.firebaseio.com) instead of the HTML page,
for a definitive existence check plus real metadata (karma, account age).

Live-verified: a real account (pg) returns a populated JSON object; a
nonexistent one returns the literal JSON value `null` with HTTP 200 (not
a 404 -- has to be checked as a body value, not a status code).
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://hacker-news.firebaseio.com/v0/user/{}.json"


class HackerNewsPlugin(SourcePlugin):
    name: ClassVar[str] = "hackernews"
    category: ClassVar[str] = "forum"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    description: ClassVar[str] = "Looks up a username via Hacker News' official, free, keyless Firebase API."

    async def run(self, identifier: Identifier) -> list[Finding]:
        url = API_URL.format(identifier.value)
        resp = await self.http.get(self.name, url)

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title="Hacker News API request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title=f"Hacker News API returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        data = resp.json()
        if data is None:  # the API's actual "not found" signal -- a bare `null` body, HTTP 200
            return []

        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=f"https://news.ycombinator.com/user?id={identifier.value}",
            title=f"Hacker News account: {identifier.value}",
            category=self.category,
            metadata={
                "karma": data.get("karma"),
                "about": data.get("about"),
                "created": data.get("created"),
                "submission_count": len(data.get("submitted") or []),
            },
            evidence_path=resp.evidence_path,
        )]
