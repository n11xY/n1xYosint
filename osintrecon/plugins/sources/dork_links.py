"""No-API "manual investigation" link generator -- accepts a full name and
returns ready-to-click search-engine URLs for LinkedIn and X (Twitter)
profile lookups, using each engine's own `site:` dork syntax.

This exists specifically because general web search (Google Custom Search,
Bing, Brave) stays out of this tool: Google and Brave both have a ToS
conflict with this project's export/caching behavior (investigated and
declined this session), and Bing's Web Search API was retired by Microsoft
in 2025. Without a working search API to run a dork query through,
`site:linkedin.com/in`-style dorking has nothing to execute against -- so
instead of calling any third-party search API, this module just constructs
the query URL and hands it to the human to open themselves. No network
call, no API key, no ToS surface, no way for it to fail.

These are suggestions, not findings -- always `MatchStatus.UNCERTAIN` (same
"a keyword lead isn't proof of identity" reasoning as search_api.py and
github_name_search.py) and reported under their own `search-lead` category
so they read distinctly from an actual matched account in any per-category
listing.
"""
from __future__ import annotations

from typing import ClassVar
from urllib.parse import urlencode

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

SEARCH_URL = "https://www.google.com/search"

_NOTE = (
    "No API call was made -- open this search link yourself. General web "
    "search is not integrated in this tool (see README)."
)


def _dork_url(dork: str) -> str:
    return f"{SEARCH_URL}?{urlencode({'q': dork})}"


class DorkLinksPlugin(SourcePlugin):
    name: ClassVar[str] = "dork_links"
    category: ClassVar[str] = "search-lead"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.NAME}
    description: ClassVar[str] = (
        "Builds ready-to-click site:linkedin.com/in and site:x.com search-engine URLs for a "
        "name -- no API call, no key, just a suggested manual search."
    )

    async def run(self, identifier: Identifier) -> list[Finding]:
        name = identifier.value
        return [
            Finding(
                source=self.name,
                identifier=identifier,
                status=MatchStatus.UNCERTAIN,
                source_url=_dork_url(f'site:linkedin.com/in "{name}"'),
                title=f'Suggested manual search: LinkedIn profiles for "{name}"',
                category=self.category,
                metadata={"platform": "linkedin", "note": _NOTE},
            ),
            Finding(
                source=self.name,
                identifier=identifier,
                status=MatchStatus.UNCERTAIN,
                source_url=_dork_url(f'site:x.com "{name}"'),
                title=f'Suggested manual search: X (Twitter) profiles for "{name}"',
                category=self.category,
                metadata={"platform": "x", "note": _NOTE},
            ),
        ]
