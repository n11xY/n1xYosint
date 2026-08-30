"""Bridge to Sherlock's site database (github.com/sherlock-project/sherlock,
MIT licensed) for username enumeration across ~480 sites.

Rather than hand-copying and manually re-verifying 480 site definitions
into our own config/sites.json (which would go stale the moment Sherlock's
community fixes the next broken one), this fetches Sherlock's own
actively-maintained data.json at runtime -- same technique as the holehe
bridge -- and interprets it directly. The fetch goes through our normal
response cache, so it's not re-downloaded on every run.

Supports Sherlock's three check styles:
  - status_code: existence is a specific HTTP status (404 by default,
    overridable via "errorCode")
  - message: a marker string (or list of marker strings, OR-matched) present
    in the response body means "not found"
  - response_url: after following redirects, landing on/at "errorUrl" means
    "not found" (used by sites that redirect unknown users to a generic page)

Also supports Sherlock's request_method/request_payload/headers (POST-based
checks, e.g. Discord's own username-availability endpoint) and regexCheck
(skip the site entirely if the username doesn't match its expected format,
rather than wasting a request).

NSFW-flagged sites are excluded by default (config: include_nsfw: true to
include them).
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, ClassVar, Optional

from osintrecon.core.logging_setup import get_logger
from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

log = get_logger("sherlock_bridge")

DATA_URL = "https://raw.githubusercontent.com/sherlock-project/sherlock/master/sherlock_project/resources/data.json"

_sites_cache: Optional[list[dict]] = None


def _substitute(obj: Any, value: str) -> Any:
    """Recursively replaces the '{}' placeholder anywhere in a string,
    dict, or list -- Sherlock's request_payload nests it inside JSON
    bodies, not just URLs."""
    if isinstance(obj, str):
        return obj.replace("{}", value)
    if isinstance(obj, dict):
        return {k: _substitute(v, value) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute(v, value) for v in obj]
    return obj


class SherlockBridgePlugin(SourcePlugin):
    name: ClassVar[str] = "sherlock"
    category: ClassVar[str] = "social"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    description: ClassVar[str] = (
        "Checks ~480 sites for username existence via Sherlock's live, "
        "community-maintained site database (github.com/sherlock-project/sherlock)."
    )

    async def _load_sites(self) -> list[dict]:
        global _sites_cache
        if _sites_cache is not None:
            return _sites_cache

        resp = await self.http.get(self.name, DATA_URL)
        if resp.error is not None or resp.status != 200:
            log.error("failed to fetch Sherlock site database: status=%s error=%s", resp.status, resp.error)
            _sites_cache = []
            return _sites_cache

        raw = resp.json() or {}
        raw.pop("$schema", None)

        include_nsfw = bool(self.config.get("include_nsfw", False))
        sites = []
        for site_name, site in raw.items():
            if not isinstance(site, dict) or "url" not in site or "errorType" not in site:
                continue
            if site.get("isNSFW") and not include_nsfw:
                continue
            site["_name"] = site_name
            sites.append(site)

        _sites_cache = sites
        log.info("loaded %d sites from Sherlock's database%s", len(sites),
                  "" if include_nsfw else " (NSFW sites excluded)")
        return sites

    async def run(self, identifier: Identifier) -> list[Finding]:
        sites = await self._load_sites()
        if not sites:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=DATA_URL, title="Sherlock site database unavailable", category=self.category,
            )]

        sem = asyncio.Semaphore(int(self.config.get("concurrency", 25)))

        async def bound_check(site: dict) -> Optional[Finding]:
            async with sem:
                return await self._check_site(site, identifier)

        results = await asyncio.gather(*(bound_check(site) for site in sites))
        return [f for f in results if f is not None]

    async def _check_site(self, site: dict, identifier: Identifier) -> Optional[Finding]:
        site_name = site["_name"]

        regex = site.get("regexCheck")
        if regex:
            try:
                if not re.match(regex, identifier.value):
                    return None  # this site's username format doesn't allow this value; not applicable
            except re.error:
                pass  # malformed regex upstream -- don't let it block the check

        probe_url = (site.get("urlProbe") or site["url"]).replace("{}", identifier.value)
        display_url = site["url"].replace("{}", identifier.value)
        method = site.get("request_method", "GET").upper()
        source_name = f"{self.name}:{site_name}"

        headers = site.get("headers")
        json_body = None
        if method == "POST" and "request_payload" in site:
            json_body = _substitute(site["request_payload"], identifier.value)

        resp = await self.http.request(
            source_name, method, probe_url,
            headers=headers, json_body=json_body,
            expected_statuses={404, 410, site.get("errorCode", 404)},
        )

        if resp.error is not None:
            return Finding(
                source=source_name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=probe_url, title=f"{site_name}: request failed", category=self.category,
                metadata={"error": resp.error},
            )

        error_type = site.get("errorType")
        found: Optional[bool] = None
        confidence_status = MatchStatus.CONFIRMED

        if error_type == "status_code":
            not_found_code = site.get("errorCode", 404)
            if resp.status == not_found_code:
                found = False
            elif resp.status == 200:
                found = True
            # any other status (500, unexpected 3xx, ...) is inconclusive -> found stays None

        elif error_type == "message":
            # A 4xx/5xx here (bot challenge, WAF block, real error) means the
            # page we got back isn't the normal "profile" or "not found" page
            # this site's errorMsg markers were written against -- content
            # analysis of it would be meaningless, so treat as inconclusive
            # rather than risk reading a block page as a real match.
            if resp.status >= 400:
                found = None
            else:
                markers = site.get("errorMsg", [])
                if isinstance(markers, str):
                    markers = [markers]
                found = not any(m in resp.text for m in markers if m)
                confidence_status = MatchStatus.PROBABLE

        elif error_type == "response_url":
            # Same reasoning: only trust the redirect-target comparison when
            # the request actually succeeded normally, not when it was
            # blocked/errored before Sherlock's expected redirect behavior
            # could even happen.
            if resp.status >= 400:
                found = None
            else:
                error_url = site.get("errorUrl", "").replace("{}", identifier.value)
                found = bool(error_url) and resp.url.rstrip("/") != error_url.rstrip("/")

        if found is not True:
            return None  # not found, or inconclusive -- don't clutter results with either

        return Finding(
            source=source_name,
            identifier=identifier,
            status=confidence_status,
            source_url=display_url,
            title=f"{site_name} profile found for '{identifier.value}'",
            category=self.category,
            metadata={"platform": site_name, "http_status": resp.status, "via": "sherlock database"},
            evidence_path=resp.evidence_path,
        )
