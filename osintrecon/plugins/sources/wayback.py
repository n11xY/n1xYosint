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

It also accepts usernames, for platforms that won't answer this tool
directly. Instagram serves anonymous requests (always from datacenter IPs,
and lately from home connections too) a login redirect or one identical
error shell for real and nonexistent accounts alike, so username_sites
can't tell. The archive can still say whether a profile URL was ever
captured: live-verified for a real account (cristiano: captures from 2012
through 2026) and a fresh random username (`[]`). That is only ever
reported as UNCERTAIN -- a capture shows the URL served a page at some
point, not that the account exists today or belongs to the person being
investigated -- with the latest capture linked so a human can look.
Instagram is never contacted by this path.

Config:
  sources:
    wayback:
      profile_urls:            # platform name -> profile URL template
        Instagram: "instagram.com/{}/"
"""
from __future__ import annotations

import re
from typing import ClassVar
from urllib.parse import quote

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

DEFAULT_PROFILE_URLS = {"Instagram": "instagram.com/{}/"}


def _format_ts(ts: str) -> str:
    return f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}" if len(ts) >= 8 else ts


class WaybackPlugin(SourcePlugin):
    name: ClassVar[str] = "wayback"
    category: ClassVar[str] = "archive"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.URL, IdentifierType.USERNAME}
    description: ClassVar[str] = (
        "Checks the Wayback Machine's free CDX API for historical snapshots of a "
        "discovered URL (e.g. a profile's linked blog/website), or of a username's "
        "profile URL on platforms that can't be checked directly (Instagram)."
    )

    async def run(self, identifier: Identifier) -> list[Finding]:
        if identifier.type == IdentifierType.USERNAME:
            return await self._run_profiles(identifier)
        if not _LOOKS_LIKE_URL_RE.match(identifier.value.strip()):
            return []
        return await self._run_url(identifier)

    async def _run_profiles(self, identifier: Identifier) -> list[Finding]:
        templates = self.config.get("profile_urls") or DEFAULT_PROFILE_URLS
        findings: list[Finding] = []
        for platform, template in templates.items():
            # safe="" so a value can never add a path segment of its own.
            profile_url = template.format(quote(identifier.value, safe=""))
            # Newest captures first matter most for a profile, so ask for
            # the last 25 capture days rather than the first 25.
            resp, snapshots = await self._snapshots(profile_url, limit=-25)
            if resp.error is not None or resp.status != 200:
                findings.append(self._error(identifier, resp, f"Wayback Machine ({platform})"))
                continue
            if not snapshots:
                continue
            first_ts, last_ts, original = snapshots[0][1], snapshots[-1][1], snapshots[-1][2]
            findings.append(Finding(
                source=f"{self.name}:{platform}",
                identifier=identifier,
                status=MatchStatus.UNCERTAIN,
                source_url=f"https://web.archive.org/web/{last_ts}/{original}",
                title=(
                    f"{platform}: profile URL archived (latest capture {_format_ts(last_ts)}) -- "
                    "open it to see whether it's a real profile"
                ),
                category="social",
                metadata={
                    "platform": platform,
                    "archived_url": profile_url,
                    "snapshot_days_seen": len(snapshots),
                    "earliest_in_window": _format_ts(first_ts),
                    "last_archived": _format_ts(last_ts),
                },
                evidence_path=resp.evidence_path,
            ))
        return findings

    async def _snapshots(self, url: str, *, limit: int):
        params = {
            "url": url,
            "output": "json",
            "limit": limit,
            "filter": "statuscode:200",
            "collapse": "timestamp:8",
        }
        resp = await self.http.get(self.name, CDX_URL, params=params, expected_statuses={503})
        if resp.error is not None or resp.status != 200:
            return resp, []
        rows = resp.json() or []
        snapshots = rows[1:] if rows and rows[0] and rows[0][0] == "urlkey" else []
        return resp, snapshots

    def _error(self, identifier: Identifier, resp, label: str) -> Finding:
        if resp.error is not None:
            return Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=CDX_URL, title=f"{label} request failed", category=self.category,
                metadata={"error": resp.error},
            )
        return Finding(
            source=self.name, identifier=identifier, status=MatchStatus.ERROR,
            source_url=CDX_URL, title=f"{label} returned {resp.status}", category=self.category,
            metadata={"http_status": resp.status},
        )

    async def _run_url(self, identifier: Identifier) -> list[Finding]:
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
