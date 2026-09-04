"""Wikipedia source module -- uses the official, free, keyless Wikipedia
OpenSearch API to check whether a full name matches a notable public
figure's article.

A match only confirms Wikipedia has an article with that (or a very
similar) title -- not that it's the specific person being investigated;
common names can collide with an unrelated notable figure who happens to
share it, and OpenSearch does prefix/fuzzy matching, not exact identity
confirmation. Always reported as UNCERTAIN, same reasoning as
github_name_search.py and the existing precedent in search_api.py ("a
keyword hit isn't proof of identity").

Live-verified: searching "Linus Torvalds" returns his article; a random
nonexistent name returns an empty result set, not an error.
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://en.wikipedia.org/w/api.php"
MAX_RESULTS = 3


class WikipediaPlugin(SourcePlugin):
    name: ClassVar[str] = "wikipedia"
    category: ClassVar[str] = "profile-directory"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.NAME}
    description: ClassVar[str] = (
        "Checks Wikipedia's official, free, keyless OpenSearch API for an article matching "
        "a full name -- always UNCERTAIN, a title match alone is never proof of identity."
    )

    async def run(self, identifier: Identifier) -> list[Finding]:
        params = {
            "action": "opensearch",
            "search": identifier.value,
            "limit": MAX_RESULTS,
            "namespace": 0,
            "format": "json",
        }
        resp = await self.http.get(self.name, API_URL, params=params)

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=API_URL, title="Wikipedia API request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=API_URL, title=f"Wikipedia API returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        data = resp.json()
        # OpenSearch's response is a positional array, not an object:
        # [query, [titles], [descriptions], [urls]].
        if not isinstance(data, list) or len(data) < 4:
            return []
        titles, descriptions, urls = data[1] or [], data[2] or [], data[3] or []

        return [
            Finding(
                source=self.name,
                identifier=identifier,
                status=MatchStatus.UNCERTAIN,
                source_url=url,
                title=f"Wikipedia article: {title}",
                category=self.category,
                metadata={"description": descriptions[i] if i < len(descriptions) else None},
                evidence_path=resp.evidence_path,
            )
            for i, (title, url) in enumerate(zip(titles, urls))
        ]
