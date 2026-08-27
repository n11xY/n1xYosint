"""Generic username-enumeration module driven by a site database (config/sites.json).

Each entry describes one platform's profile URL pattern and a cheap heuristic
for telling "profile exists" from "profile does not exist" -- either an HTTP
status code check (reliable, => CONFIRMED) or a substring match in the page
body (heuristic, => PROBABLE, since markup changes can produce false results).

This mirrors the approach used by well-known, widely-adopted OSINT tools
(Sherlock, WhatsMyName): it only ever requests the public profile URL that a
normal browser would load and never attempts to bypass any access control.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import ClassVar

from osintrecon.core.logging_setup import get_logger
from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

log = get_logger("username_sites")

DEFAULT_SITES_PATH = Path(__file__).resolve().parents[3] / "config" / "sites.json"


def _load_sites(path: Path) -> list[dict]:
    if not path.exists():
        log.warning("site database not found: %s", path)
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log.error("malformed site database %s: %s", path, exc)
        return []


class UsernameSitesPlugin(SourcePlugin):
    name: ClassVar[str] = "username_sites"
    category: ClassVar[str] = "social"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    description: ClassVar[str] = "Checks a configurable database of public profile URLs for username existence."

    def __init__(self, config: dict, http):
        super().__init__(config, http)
        sites_path = Path(config.get("sites_db") or DEFAULT_SITES_PATH)
        self.sites = _load_sites(sites_path)

    async def run(self, identifier: Identifier) -> list[Finding]:
        results = await asyncio.gather(*(self._check_site(site, identifier) for site in self.sites))
        return [finding for finding in results if finding is not None]

    async def _check_site(self, site: dict, identifier: Identifier) -> Finding | None:
        url = site["url"].format(identifier.value)
        source_name = f"{self.name}:{site['name']}"
        resp = await self.http.get(source_name, url, expected_statuses={404})

        if resp.error is not None:
            return Finding(
                source=source_name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title=f"{site['name']}: request failed",
                category=site.get("category", self.category),
                metadata={"error": resp.error},
            )

        check_type = site.get("check", "status")
        if check_type == "status":
            found = resp.status == site.get("found_status", 200)
            status = MatchStatus.CONFIRMED if found else MatchStatus.NOT_FOUND
        else:  # message-based heuristic
            not_found_text = site.get("not_found_text", "")
            found = resp.status < 400 and not_found_text and not_found_text not in resp.text
            status = MatchStatus.PROBABLE if found else MatchStatus.NOT_FOUND

        if status == MatchStatus.NOT_FOUND:
            return None  # don't clutter results with every non-match

        return Finding(
            source=source_name,
            identifier=identifier,
            status=status,
            source_url=url,
            title=f"{site['name']} profile found for '{identifier.value}'",
            category=site.get("category", self.category),
            metadata={"platform": site["name"], "http_status": resp.status, "cached": resp.cached},
            evidence_path=resp.evidence_path,
        )
