"""Codeforces source module -- uses the official, free, keyless Codeforces
API (codeforces.com/api/user.info) instead of the HTML profile page. The
HTML page (codeforces.com/profile/{handle}) returns 403 for anonymous
requests (confirmed while curl-testing username_sites candidates), but this
JSON API is a separate, unblocked origin.

Live-verified: a real handle (tourist) returns HTTP 200 with
{"status": "OK", "result": [...]}; a nonexistent handle returns HTTP 400
with {"status": "FAILED", "comment": "handles: User with handle ... not
found"} -- Codeforces' own documented shape for "no such user", not an
error worth surfacing as one.
"""
from __future__ import annotations

from typing import ClassVar
from urllib.parse import quote

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://codeforces.com/api/user.info?handles={}"


class CodeforcesPlugin(SourcePlugin):
    name: ClassVar[str] = "codeforces"
    category: ClassVar[str] = "code-hosting"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    description: ClassVar[str] = "Looks up a handle via the official, free, keyless Codeforces API."

    async def run(self, identifier: Identifier) -> list[Finding]:
        url = API_URL.format(quote(identifier.value, safe=""))
        resp = await self.http.get(self.name, url, expected_statuses={400})

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title="Codeforces API request failed", category=self.category,
                metadata={"error": resp.error},
            )]

        data = resp.json() or {}
        if data.get("status") != "OK":
            return []  # documented "no such handle" shape (HTTP 400 + status: FAILED)

        result = (data.get("result") or [{}])[0]
        city = result.get("city")
        country = result.get("country")
        location = ", ".join(part for part in (city, country) if part) or None

        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=f"https://codeforces.com/profile/{identifier.value}",
            title=f"Codeforces account: {result.get('handle', identifier.value)} ({result.get('rank', 'unrated')})",
            category=self.category,
            metadata={
                "company": result.get("organization"),
                "location": location,
                "country": country,
                "rank": result.get("rank"),
                "rating": result.get("rating"),
                "max_rank": result.get("maxRank"),
            },
            evidence_path=resp.evidence_path,
        )]
