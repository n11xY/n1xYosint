"""Generic username-enumeration module driven by a site database (config/sites.json).

Each entry describes one platform's profile URL pattern and a cheap heuristic
for telling "profile exists" from "profile does not exist" -- either an HTTP
status code check (reliable, => CONFIRMED) or a substring match in the page
body (heuristic, => PROBABLE, since markup changes can produce false results).

This mirrors the approach used by well-known, widely-adopted OSINT tools
(Sherlock, WhatsMyName): it only ever requests the public profile URL that a
normal browser would load and never attempts to bypass any access control.

Every apparent match also gets cross-checked against a decoy: a short,
random, effectively-guaranteed-nonexistent username, probed against the
exact same site right after. If the decoy *also* looks "found," that
site's found/not-found signal isn't discriminating anything right now (a
Cloudflare bot challenge serving the same response to everyone, a global
outage, a site that changed its 404 behavior, ...) -- exactly the failure
mode this project has independently caught by hand before shipping a new
site (see CONTRIBUTING.md's verification requirement), now automated at
query time instead of only at curation time. Rather than silently report
a false CONFIRMED/PROBABLE in that case, the result is downgraded to
MatchStatus.UNCERTAIN with a metadata note explaining why, so a human
reviewer knows to double-check it instead of trusting it outright. The
decoy request is only ever spent on sites that looked like a hit -- a
NOT_FOUND result costs exactly what it always did.
"""
from __future__ import annotations

import asyncio
import json
import secrets
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


def _decoy_username() -> str:
    # Short enough to fit under most platforms' own username length caps
    # (some sites in config/sites.json cap around 15 characters), clearly
    # synthetic, and effectively guaranteed not to exist anywhere -- a
    # fresh one is generated per run, never reused or hardcoded, so no
    # site could ever end up special-casing a known decoy string.
    return f"nx1{secrets.token_hex(6)}"


def _is_found(resp, site: dict) -> bool:
    check_type = site.get("check", "status")
    if check_type == "status":
        return resp.status == site.get("found_status", 200)
    not_found_text = site.get("not_found_text", "")
    return resp.status < 400 and bool(not_found_text) and not_found_text not in resp.text


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
        # One decoy per run (not per site) -- it doesn't need to be
        # per-site-unique, only unpredictable and unregistered, and reusing
        # it keeps this to one extra random value instead of 90+.
        decoy = _decoy_username()
        results = await asyncio.gather(*(self._check_site(site, identifier, decoy) for site in self.sites))
        return [finding for finding in results if finding is not None]

    async def _check_site(self, site: dict, identifier: Identifier, decoy: str) -> Finding | None:
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
        found = _is_found(resp, site)
        status = (MatchStatus.CONFIRMED if check_type == "status" else MatchStatus.PROBABLE) if found else MatchStatus.NOT_FOUND

        if status == MatchStatus.NOT_FOUND:
            return None  # don't clutter results with every non-match

        metadata = {"platform": site["name"], "http_status": resp.status, "cached": resp.cached}
        title = f"{site['name']} profile found for '{identifier.value}'"

        decoy_url = site["url"].format(decoy)
        decoy_resp = await self.http.get(source_name, decoy_url, expected_statuses={404})
        if decoy_resp.error is not None:
            metadata["decoy_check"] = "skipped (decoy request failed)"
        elif _is_found(decoy_resp, site):
            metadata["decoy_check"] = "failed -- a random nonexistent username also matched on this site just now"
            status = MatchStatus.UNCERTAIN
            title = (
                f"{site['name']}: possible match for '{identifier.value}' "
                "(unverified -- this site's found/not-found signal looked unreliable this run)"
            )
        else:
            metadata["decoy_check"] = "passed"

        return Finding(
            source=source_name,
            identifier=identifier,
            status=status,
            source_url=url,
            title=title,
            category=site.get("category", self.category),
            metadata=metadata,
            evidence_path=resp.evidence_path,
        )
