"""GitHub commit-author email source module -- uses GitHub's public commit
search API (search/commits, no auth required) to find a real email address
tied to a username's commit history. Many users hide their email in their
GitHub profile settings, but every git commit still carries the author
email from their local git config -- if it's a personal address (not
GitHub's auto-generated @users.noreply.github.com), it's sitting right
there in the commit objects GitHub's own API already returns. Purely
passive: this only reads GitHub's documented public search API, the same
one anyone gets by typing "author:someone" into github.com/search.

Live-verified against real accounts (torvalds, octocat) before shipping.
Notably, GitHub's /users/{username}/events/public feed no longer includes
a commits[] array in PushEvent payloads (trimmed at some point, presumably
for exactly this reason) -- an earlier draft of this module assumed that
endpoint still worked and was wrong; the commit Search API is the one that
actually still exposes commit.author.email.
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

SEARCH_URL = "https://api.github.com/search/commits"

# GitHub's own auto-generated "keep my email address private" address --
# not a real-world leak, so it's filtered out rather than reported as one.
_NOREPLY_SUFFIX = "@users.noreply.github.com"


class GitHubCommitEmailPlugin(SourcePlugin):
    name: ClassVar[str] = "github_commit_email"
    category: ClassVar[str] = "code-hosting"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    description: ClassVar[str] = (
        "Searches GitHub's public commit search API for a real commit-author email "
        "tied to a username, even when it's hidden from their profile."
    )

    async def run(self, identifier: Identifier) -> list[Finding]:
        headers = {"Accept": "application/vnd.github+json"}
        token = self.config.get("api_key")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        # GitHub's commit-search qualifier: matches commits GitHub has
        # attributed to this username, across every public repo (not just
        # ones they own) -- e.g. forks/mirrors of a project they contribute
        # to. sort=author-date/desc so results skew toward recent activity.
        params = {
            "q": f"author:{identifier.value}",
            "per_page": 10,
            "sort": "author-date",
            "order": "desc",
        }

        resp = await self.http.get(
            self.name, SEARCH_URL, headers=headers, params=params, expected_statuses={403, 422},
        )

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=SEARCH_URL, title="GitHub commit search request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        # The commit Search API has its own, much tighter rate limit (10/min
        # unauthenticated, separate from the general REST API's 60/hour) --
        # 403 here almost always means that, not an auth problem.
        if resp.status == 403:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=SEARCH_URL, title="GitHub search API rate-limited", category=self.category,
                metadata={"http_status": 403},
            )]
        if resp.status != 200:
            return []

        items = (resp.json() or {}).get("items") or []
        seen: dict[str, dict] = {}
        for item in items:
            author = (item.get("commit") or {}).get("author") or {}
            email = (author.get("email") or "").strip().lower()
            if not email or email.endswith(_NOREPLY_SUFFIX) or email in seen:
                continue
            seen[email] = {
                "author_name": author.get("name"),
                "commit_date": author.get("date"),
                "repo": (item.get("repository") or {}).get("full_name"),
                "commit_url": item.get("html_url"),
            }

        return [
            Finding(
                source=self.name,
                identifier=identifier,
                status=MatchStatus.CONFIRMED,
                source_url=meta["commit_url"] or SEARCH_URL,
                title=f"Commit-author email found: {email}",
                category=self.category,
                metadata=meta,
                discovered_identifiers=[Identifier(value=email, type=IdentifierType.EMAIL)],
                evidence_path=resp.evidence_path,
            )
            for email, meta in seen.items()
        ]
