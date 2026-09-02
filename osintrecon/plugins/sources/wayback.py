"""Wayback Machine (archive.org) source module -- checks the Internet
Archive's free, keyless CDX API for historical snapshots of a URL. No
authentication, no rate-limit tier, fully documented
(https://archive.org/help/wayback_api.php).

This is the first plugin that accepts IdentifierType.URL: github.py has
discovered a profile's "blog" field as a URL identifier for a while, but
nothing consumed it -- no plugin's `accepts` set included URL, so it was a
dead end (confirmed via grep across every other source module). Checking
a discovered URL's archive history is a natural fit: it surfaces whether
a page's content changed or was taken down over time, which the live page
alone can never show.

Live-verified before shipping: real snapshots for a known-archived URL
came back correctly shaped, and a URL with zero snapshots returns a
clean empty JSON array (`[]`), not an error or a 404.
"""
from __future__ import annotations

import re
from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

CDX_URL = "https://web.archive.org/cdx/search/cdx"

# Light sanity check, not full URL validation: github.py populates this
# identifier type from a GitHub profile's freeform "blog" text field, which
# isn't validated as a real URL by GitHub itself (or by this codebase --
# normalize.py has no URL branch, since URL identifiers are only ever
# plugin-discovered, never user-typed on the CLI). Reject obvious non-URLs
# (no dot, contains whitespace) before spending an API call on them.
_LOOKS_LIKE_URL_RE = re.compile(r"^\S+\.\S+$")


def _format_ts(ts: str) -> str:
    return f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}" if len(ts) >= 8 else ts


class WaybackPlugin(SourcePlugin):
    name: ClassVar[str] = "wayback"
    category: ClassVar[str] = "archive"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.URL}
    description: ClassVar[str] = (
        "Checks the Wayback Machine's free CDX API for historical snapshots of a "
        "discovered URL (e.g. a profile's linked blog/website)."
    )

    async def run(self, identifier: Identifier) -> list[Finding]:
        if not _LOOKS_LIKE_URL_RE.match(identifier.value.strip()):
            return []

        params = {
            "url": identifier.value,
            "output": "json",
            "limit": 25,
            "filter": "statuscode:200",
            # One entry per calendar day (first 8 digits of the 14-digit
            # YYYYMMDDhhmmss timestamp) -- without this a frequently-crawled
            # page returns dozens of near-identical same-day snapshots.
            "collapse": "timestamp:8",
        }
        resp = await self.http.get(self.name, CDX_URL, params=params, expected_statuses={503})

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=CDX_URL, title="Wayback Machine request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=CDX_URL, title=f"Wayback Machine returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        rows = resp.json() or []
        # First row is a column-name header ("urlkey", "timestamp", ...),
        # not a snapshot -- CDX's json output format, not a list of objects.
        snapshots = rows[1:] if rows and rows[0] and rows[0][0] == "urlkey" else []
        if not snapshots:
            return []

        first_ts, original = snapshots[0][1], snapshots[0][2]
        last_ts = snapshots[-1][1]

        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=f"https://web.archive.org/web/{last_ts}/{original}",
            title=f"Archived {len(snapshots)} time(s) between {_format_ts(first_ts)} and {_format_ts(last_ts)}",
            category=self.category,
            metadata={
                "snapshot_days_seen": len(snapshots),
                "hit_page_limit": len(snapshots) >= params["limit"],
                "first_archived": _format_ts(first_ts),
                "last_archived": _format_ts(last_ts),
                "earliest_snapshot_url": f"https://web.archive.org/web/{first_ts}/{original}",
                "latest_snapshot_url": f"https://web.archive.org/web/{last_ts}/{original}",
            },
            evidence_path=resp.evidence_path,
        )]
