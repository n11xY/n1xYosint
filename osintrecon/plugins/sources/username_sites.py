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

Per-site settings (config: sources.username_sites.site_settings.<site name>)
let one site be handled differently from the rest without a module of its
own. Instagram is the site that needs it (see config.py for its defaults):
a site can be switched off; given its own request policy in the HTTP client
(concurrency, timeout, spacing between requests, retries, 429 cooldown --
see core/http_client.py); have its decoy check turned off; and cache what
each lookup *concluded* -- "found" for cache_ttl, "not found" for
negative_cache_ttl -- instead of caching raw pages, which for Instagram are
often login walls or error shells that say nothing about the account.

Every finding carries metadata["outcome"] (profile_found, not_found,
rate_limited, access_restricted, network_error or unknown) and
metadata["freshness"]: "fresh" (collected now), "cached" (from a cache, with
the original collection time), "stale" (a found profile past its cache TTL,
shown only because the site can't be re-checked right now) or "unavailable"
(no usable answer this time; the outcome says why). A site with "found_text"
is classified strictly: only its profile markers count as found, only an
explicitly configured not-found signal counts as not found, and a login
wall, challenge page, rate limit or any other unexpected page is reported as
unavailable -- never as a nonexistent account, and never decided by the HTTP
status alone.
"""
from __future__ import annotations

import asyncio
import html
import json
import random
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, ClassVar, Optional
from urllib.parse import quote

from osintrecon.core.http_client import SourcePolicy
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


# Status codes that mean "the real answer is unknown" (rate-limited,
# temporarily down) rather than a genuine answer. Without this,
# _is_found() below treats any non-found status as a plain non-match --
# a 429 from a platform that's currently rate-limiting this server's IP
# would silently report a real account as nonexistent instead of flagging
# that the check simply couldn't run. No entry in config/sites.json uses
# any of these as its actual found_status/not_found_status (confirmed by
# grepping the file), so this can't misfire against a real signal.
_INCONCLUSIVE_STATUSES = {429, 500, 502, 503, 504}

# Random pause before each request -- see _lookup().
_JITTER_SECONDS = (0.2, 0.8)

# This module's whole design (see the class docstring above) is "request
# the exact page a normal browser would load" -- but the base HTTP client's
# default headers are just a self-identifying User-Agent and nothing else
# (honest, and correct for the official-API plugins elsewhere in this
# codebase, but an unusually bare signal for a plugin that's specifically
# trying to look like an ordinary pageview). Overridden here, for this
# plugin only, with what a current desktop Chrome actually sends on a GET.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class LookupOutcome(str, Enum):
    """What one lookup established about one username on one site."""

    PROFILE_FOUND = "profile_found"
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    ACCESS_RESTRICTED = "access_restricted"   # login wall, challenge page, 401/403
    NETWORK_ERROR = "network_error"
    UNKNOWN = "unknown"                       # an answer that is neither found nor not-found


# Outcomes that say nothing about whether the account exists.
_UNAVAILABLE = {
    LookupOutcome.RATE_LIMITED,
    LookupOutcome.ACCESS_RESTRICTED,
    LookupOutcome.NETWORK_ERROR,
    LookupOutcome.UNKNOWN,
}


@dataclass(frozen=True)
class SiteSettings:
    """Per-site overrides from sources.username_sites.site_settings.<name>."""

    enabled: bool = True
    decoy_verification_enabled: bool = True
    # With cache_ttl set, what each lookup concluded is cached and this
    # site's raw responses are not; without it the site uses the shared
    # HTTP response cache like every other site.
    cache_ttl: Optional[float] = None
    negative_cache_ttl: Optional[float] = None
    # How long past its cache_ttl a found profile may still be shown --
    # marked stale -- while the site can't be re-checked.
    stale_ttl: float = 0.0
    policy: Optional[SourcePolicy] = None

    @classmethod
    def from_config(cls, raw: dict[str, Any]) -> "SiteSettings":
        def number(key: str, minimum: float = 0.0) -> Optional[float]:
            value = raw.get(key)
            if value is None:
                return None
            try:
                return max(minimum, float(value))
            except (TypeError, ValueError):
                log.warning("ignoring username_sites site setting %s=%r: not a number", key, value)
                return None

        # Config names (what a user writes) -> SourcePolicy fields.
        policy_fields = {
            "max_concurrency": number("max_concurrency", 1),
            "request_timeout": number("request_timeout"),
            "min_interval": number("minimum_request_interval"),
            "max_retries": number("max_retry_attempts"),
            "cooldown": number("cooldown_duration"),
            "max_cooldown": number("max_cooldown_duration"),
            "max_retry_wait": number("max_retry_wait"),
        }
        given: dict[str, Any] = {name: value for name, value in policy_fields.items() if value is not None}
        policy = None
        if given:
            for name in ("max_concurrency", "max_retries"):
                if name in given:
                    given[name] = int(given[name])
            policy = SourcePolicy(**given)
            if policy.max_cooldown < policy.cooldown:
                policy.max_cooldown = policy.cooldown
        return cls(
            enabled=bool(raw.get("enabled", True)),
            decoy_verification_enabled=bool(raw.get("decoy_verification_enabled", True)),
            cache_ttl=number("cache_ttl"),
            negative_cache_ttl=number("negative_cache_ttl"),
            stale_ttl=number("stale_ttl") or 0.0,
            policy=policy,
        )


_DEFAULT_SETTINGS = SiteSettings()


@dataclass
class _Lookup:
    """One site's answer for one (normalized) username, shared by every
    identifier that normalizes to it."""

    outcome: LookupOutcome
    freshness: str                              # fresh | cached | stale | unavailable
    status: Optional[MatchStatus] = None        # for a found profile
    http_status: Optional[int] = None
    collected_at: Optional[float] = None
    expires_at: Optional[float] = None
    decoy_check: Optional[str] = None
    final_url: str = ""
    error: Optional[str] = None
    cooldown_until: Optional[float] = None
    request_sent: bool = True
    evidence_path: Optional[str] = None
    stale_reason: Optional[LookupOutcome] = None


def _decoy_username() -> str:
    # Short enough to fit under most platforms' own username length caps
    # (some sites in config/sites.json cap around 15 characters), clearly
    # synthetic, and effectively guaranteed not to exist anywhere -- a
    # fresh one is generated per run, never reused or hardcoded, so no
    # site could ever end up special-casing a known decoy string.
    return f"nx1{secrets.token_hex(6)}"


def _iso(epoch: Optional[float]) -> Optional[str]:
    if not epoch:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds")


def _readable_time(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _profile_markers(site: dict, username: str) -> list[str]:
    markers = site.get("found_text") or []
    if isinstance(markers, str):
        markers = [markers]
    # "{}" in a marker stands for the username being checked, so a marker can
    # require the page to be about *this* account rather than any profile.
    return [marker.replace("{}", username) for marker in markers]


def _has_profile_markers(resp, site: dict, username: str) -> bool:
    if resp.status >= 400:
        return False
    markers = _profile_markers(site, username)
    if not markers:
        return False
    text = resp.text or ""
    # Also matched against the entity-decoded page: sites write parts of a
    # title as HTML entities (Instagram has used "&#064;" for "@").
    decoded = html.unescape(text)
    return all(marker in text or marker in decoded for marker in markers)


def _has_not_found_signal(resp, site: dict) -> bool:
    not_found_text = site.get("not_found_text")
    if not_found_text and not_found_text in (resp.text or ""):
        return True
    return "not_found_status" in site and resp.status == site["not_found_status"]


def _is_found(resp, site: dict, username: str = "") -> bool:
    check_type = site.get("check", "status")
    if check_type == "status":
        return resp.status == site.get("found_status", 200)
    if site.get("found_text"):
        return _has_profile_markers(resp, site, username)
    not_found_text = site.get("not_found_text", "")
    return resp.status < 400 and bool(not_found_text) and not_found_text not in resp.text


def _classify(resp, site: dict, username: str) -> LookupOutcome:
    """One response -> one outcome. A "found_text" site is held to the
    strict rules in the module docstring; every other site keeps its
    original found / not-found reading of the status or not_found_text."""
    if getattr(resp, "error", None) is not None:
        return LookupOutcome.NETWORK_ERROR
    if resp.status == 429:
        return LookupOutcome.RATE_LIMITED
    if resp.status in _INCONCLUSIVE_STATUSES:
        return LookupOutcome.UNKNOWN
    # Some platforms (confirmed: Instagram, from datacenter IPs) answer an
    # anonymous profile request by redirecting to their login page instead
    # of showing the profile or a 404. That says nothing about whether the
    # account exists -- only that this network can't check without signing
    # in, which this tool never does.
    login_wall = site.get("login_wall")
    if login_wall and login_wall in (getattr(resp, "url", "") or ""):
        return LookupOutcome.ACCESS_RESTRICTED
    if site.get("found_text"):
        if _has_profile_markers(resp, site, username):
            return LookupOutcome.PROFILE_FOUND
        if _has_not_found_signal(resp, site):
            return LookupOutcome.NOT_FOUND
        if resp.status in (401, 403):
            return LookupOutcome.ACCESS_RESTRICTED
        # An error shell, a bot challenge, a page served identically for
        # every username: absence of the profile marker alone is not
        # evidence the account doesn't exist.
        return LookupOutcome.UNKNOWN
    return LookupOutcome.PROFILE_FOUND if _is_found(resp, site, username) else LookupOutcome.NOT_FOUND


class UsernameSitesPlugin(SourcePlugin):
    name: ClassVar[str] = "username_sites"
    category: ClassVar[str] = "social"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    description: ClassVar[str] = "Checks a configurable database of public profile URLs for username existence."

    def __init__(self, config: dict, http):
        super().__init__(config, http)
        sites_path = Path(config.get("sites_db") or DEFAULT_SITES_PATH)
        self.sites = _load_sites(sites_path)
        self.site_settings: dict[str, SiteSettings] = {}
        for site_name, raw in (config.get("site_settings") or {}).items():
            if not isinstance(raw, dict):
                log.warning("ignoring username_sites site_settings.%s: expected a mapping", site_name)
                continue
            settings = SiteSettings.from_config(raw)
            self.site_settings[site_name] = settings
            if settings.enabled and settings.policy is not None:
                self.http.set_source_policy(self._source_name(site_name), settings.policy)
        # Lookups in flight, keyed by (source, normalized username): a second
        # identifier that normalizes to the same account joins the first
        # lookup -- request, decoy check and cache write included -- instead
        # of repeating it.
        self._inflight: dict[tuple[str, str], asyncio.Task] = {}

    def _source_name(self, site_name: str) -> str:
        return f"{self.name}:{site_name}"

    def _count(self, source_name: str, counter: str) -> None:
        metrics = getattr(self.http, "source_metrics", None)
        if metrics is not None:
            setattr(metrics(source_name), counter, getattr(metrics(source_name), counter) + 1)

    async def run(self, identifier: Identifier) -> list[Finding]:
        # One decoy per run (not per site) -- it doesn't need to be
        # per-site-unique, only unpredictable and unregistered, and reusing
        # it keeps this to one extra random value instead of 90+.
        decoy = _decoy_username()
        # return_exceptions: one site's check failing in an unexpected way
        # must cost that site's result only, not the other 90+.
        results = await asyncio.gather(
            *(self._check_site(site, identifier, decoy) for site in self.sites), return_exceptions=True
        )
        findings: list[Finding] = []
        for site, result in zip(self.sites, results):
            if isinstance(result, Exception):
                findings.append(self._crash_finding(site, identifier, result))
            elif isinstance(result, BaseException):
                raise result
            elif result is not None:
                findings.append(result)
        return findings

    async def _check_site(self, site: dict, identifier: Identifier, decoy: str) -> Finding | None:
        settings = self.site_settings.get(site["name"], _DEFAULT_SETTINGS)
        if not settings.enabled:
            return None
        # Most sites treat the profile path case-insensitively (or the
        # identifier was already typed in the account's real case), but a
        # few (confirmed: Spotify, which lowercases the handle you type at
        # signup into its own account id) only match the exact lowercase
        # form -- "lowercase": true opts an entry into normalizing before
        # the request (and the cache lookup), everyone else is untouched.
        value = identifier.value.lower() if site.get("lowercase") else identifier.value
        # A site can declare which usernames it allows at all (Instagram:
        # letters, digits, "." and "_", at most 30). Anything else can't be
        # an account there -- a not-found that needs no request.
        pattern = site.get("username_pattern")
        if pattern and not re.fullmatch(pattern, value):
            return None
        # Encode before formatting into the template -- some entries put the
        # identifier in a position where an unencoded URL-structural
        # character could change which request actually gets sent.
        # safe="" so a value can never introduce a new URL path segment.
        url = site["url"].format(quote(value, safe=""))
        source_name = self._source_name(site["name"])
        lookup = await self._shared_lookup(
            source_name, value, lambda: self._lookup(site, source_name, value, url, settings, decoy)
        )
        return self._finding(site, identifier, url, lookup)

    async def _shared_lookup(self, source_name: str, value: str,
                             start: Callable[[], Awaitable[_Lookup]]) -> _Lookup:
        key = (source_name, value)
        pending = self._inflight.get(key)
        if pending is not None:
            self._count(source_name, "duplicates_prevented")
            return await asyncio.shield(pending)
        task = asyncio.ensure_future(start())
        self._inflight[key] = task
        try:
            return await task
        finally:
            self._inflight.pop(key, None)

    def _result_cache(self, settings: SiteSettings):
        return getattr(self.http, "cache", None) if settings.cache_ttl is not None else None

    async def _lookup(self, site: dict, source_name: str, value: str, url: str,
                      settings: SiteSettings, decoy: str) -> _Lookup:
        # A small, randomized pause before firing -- so the (up to
        # rate_limit_per_source) concurrent checks against one site don't
        # all leave at the exact same instant, which on its own is a fairly
        # mechanical-looking pattern regardless of what headers go out.
        await asyncio.sleep(random.uniform(*_JITTER_SECONDS))

        cache = self._result_cache(settings)
        stored = cache.get_result(source_name, value) if cache is not None else None
        if stored is not None and not stored["stale"]:
            self._count(source_name, "cache_hits")
            log.debug("%s: cached result for %s (collected %s)", source_name, value, _iso(stored["collected_at"]))
            return self._lookup_from_store(stored, "cached")

        get_kwargs = {"allow_cache": False} if settings.cache_ttl is not None else {}
        resp = await self.http.get(source_name, url, headers=_BROWSER_HEADERS, expected_statuses={404}, **get_kwargs)
        outcome = _classify(resp, site, value)
        collected_at = getattr(resp, "fetched_at", None) or time.time()

        if outcome in _UNAVAILABLE:
            if outcome is LookupOutcome.ACCESS_RESTRICTED and settings.policy is not None:
                # A login wall or challenge is the site declining to answer
                # this network right now. Asking again later in this run (or
                # in the next one) gets the same, so back off as for a 429.
                self.http.start_cooldown(source_name)
            if (stored is not None and stored["payload"].get("outcome") == LookupOutcome.PROFILE_FOUND.value
                    and time.time() <= stored["expires_at"] + settings.stale_ttl):
                lookup = self._lookup_from_store(stored, "stale")
                lookup.stale_reason = outcome
                return lookup
            return _Lookup(
                outcome=outcome, freshness="unavailable",
                http_status=resp.status or None, collected_at=collected_at,
                final_url=getattr(resp, "url", "") or "", error=getattr(resp, "error", None),
                cooldown_until=getattr(resp, "cooldown_until", None),
                request_sent=not getattr(resp, "skipped", False),
            )

        if outcome is LookupOutcome.NOT_FOUND:
            if cache is not None and settings.negative_cache_ttl:
                cache.put_result(source_name, value, {"outcome": outcome.value, "http_status": resp.status},
                                 settings.negative_cache_ttl, collected_at)
            return _Lookup(outcome=outcome, freshness="fresh", http_status=resp.status, collected_at=collected_at)

        status = MatchStatus.CONFIRMED if site.get("check", "status") == "status" else MatchStatus.PROBABLE
        if settings.decoy_verification_enabled:
            decoy_check, decoy_failed = await self._decoy_check(site, source_name, decoy, get_kwargs)
            if decoy_failed:
                status = MatchStatus.UNCERTAIN
        else:
            decoy_check = "disabled for this site"

        expires_at = None
        # A failed decoy check means this site's signal can't be trusted
        # right now -- not something to keep.
        if cache is not None and status is not MatchStatus.UNCERTAIN:
            cache.put_result(
                source_name, value,
                {"outcome": outcome.value, "status": status.value, "http_status": resp.status, "decoy_check": decoy_check},
                settings.cache_ttl, collected_at,  # type: ignore[arg-type]
            )
            expires_at = collected_at + settings.cache_ttl  # type: ignore[operator]
        return _Lookup(
            outcome=outcome, freshness="cached" if getattr(resp, "cached", False) else "fresh",
            status=status, http_status=resp.status, collected_at=collected_at, expires_at=expires_at,
            decoy_check=decoy_check, final_url=getattr(resp, "url", "") or "",
            evidence_path=getattr(resp, "evidence_path", None),
        )

    async def _decoy_check(self, site: dict, source_name: str, decoy: str,
                           get_kwargs: dict[str, Any]) -> tuple[str, bool]:
        """(metadata note, whether the decoy also looked found)."""
        decoy_url = site["url"].format(quote(decoy, safe=""))
        await asyncio.sleep(random.uniform(*_JITTER_SECONDS))
        decoy_resp = await self.http.get(source_name, decoy_url, headers=_BROWSER_HEADERS,
                                         expected_statuses={404}, **get_kwargs)
        decoy_outcome = _classify(decoy_resp, site, decoy)
        if decoy_outcome is LookupOutcome.PROFILE_FOUND:
            return "failed -- a random nonexistent username also matched on this site just now", True
        if decoy_outcome is LookupOutcome.NOT_FOUND:
            return "passed", False
        if decoy_outcome is LookupOutcome.NETWORK_ERROR:
            return "skipped (decoy request failed)", False
        # Rate-limited, blocked or unreadable: the decoy said nothing either
        # way, which is not a pass.
        return f"inconclusive (decoy request came back {decoy_outcome.value})", False

    @staticmethod
    def _lookup_from_store(stored: dict[str, Any], freshness: str) -> _Lookup:
        payload = stored["payload"]
        status = payload.get("status")
        return _Lookup(
            outcome=LookupOutcome(payload.get("outcome", LookupOutcome.UNKNOWN.value)),
            freshness=freshness,
            status=MatchStatus(status) if status else None,
            http_status=payload.get("http_status"),
            collected_at=stored["collected_at"],
            expires_at=stored["expires_at"],
            decoy_check=payload.get("decoy_check"),
            request_sent=False,
        )

    def _finding(self, site: dict, identifier: Identifier, url: str, lookup: _Lookup) -> Finding | None:
        if lookup.freshness == "unavailable":
            return self._unavailable_finding(site, identifier, url, lookup)
        if lookup.outcome is not LookupOutcome.PROFILE_FOUND:
            return None  # don't clutter results with every non-match

        site_name = site["name"]
        metadata: dict[str, Any] = {
            "platform": site_name,
            "http_status": lookup.http_status,
            "cached": lookup.freshness in ("cached", "stale"),
            "decoy_check": lookup.decoy_check,
            "outcome": lookup.outcome.value,
            "freshness": lookup.freshness,
            "collected_at": _iso(lookup.collected_at),
        }
        if lookup.expires_at:
            metadata["cache_expires_at"] = _iso(lookup.expires_at)

        status = lookup.status or MatchStatus.PROBABLE
        title = f"{site_name} profile found for '{identifier.value}'"
        if lookup.freshness == "stale":
            # Seen before, can't be re-checked now: still worth showing, but
            # only as something for a person to confirm.
            reason = lookup.stale_reason or LookupOutcome.UNKNOWN
            status = MatchStatus.UNCERTAIN
            title = (
                f"{site_name}: profile seen on {(_iso(lookup.collected_at) or '')[:10]} for '{identifier.value}' "
                f"(stale -- can't be re-checked right now: {reason.value.replace('_', ' ')})"
            )
            metadata["unavailable_reason"] = reason.value
        elif status is MatchStatus.UNCERTAIN:
            title = (
                f"{site_name}: possible match for '{identifier.value}' "
                "(unverified -- this site's found/not-found signal looked unreliable this run)"
            )

        return Finding(
            source=self._source_name(site_name),
            identifier=identifier,
            status=status,
            source_url=url,
            title=title,
            category=site.get("category", self.category),
            metadata=metadata,
            evidence_path=lookup.evidence_path,
        )

    def _unavailable_finding(self, site: dict, identifier: Identifier, url: str, lookup: _Lookup) -> Finding:
        site_name = site["name"]
        metadata: dict[str, Any] = {
            "outcome": lookup.outcome.value,
            "freshness": "unavailable",
            "checked_at": _iso(lookup.collected_at),
        }
        if lookup.outcome is LookupOutcome.NETWORK_ERROR:
            title = f"{site_name}: request failed"
            metadata["error"] = lookup.error
        elif lookup.outcome is LookupOutcome.RATE_LIMITED:
            metadata["http_status"] = 429
            metadata["request_sent"] = lookup.request_sent
            metadata["cooldown_until"] = _iso(lookup.cooldown_until)
            if not lookup.request_sent and lookup.cooldown_until:
                title = (
                    f"{site_name}: not checked -- paused until {_readable_time(lookup.cooldown_until)} "
                    "after a rate limit or sign-in wall"
                )
            else:
                title = f"{site_name}: temporarily unavailable (HTTP 429)"
                if lookup.cooldown_until:
                    title += f" -- paused until {_readable_time(lookup.cooldown_until)}"
        elif lookup.outcome is LookupOutcome.ACCESS_RESTRICTED:
            metadata["http_status"] = lookup.http_status
            login_wall = site.get("login_wall")
            if login_wall and login_wall in lookup.final_url:
                title = f"{site_name}: requires sign-in from this network -- open the link to check by hand"
                metadata["blocked"] = "login_wall"
                metadata["redirected_to"] = lookup.final_url
            else:
                title = (
                    f"{site_name}: access restricted (HTTP {lookup.http_status}) -- open the link to check by hand"
                )
                metadata["blocked"] = "access_restricted"
        else:
            metadata["http_status"] = lookup.http_status
            if lookup.http_status in _INCONCLUSIVE_STATUSES:
                title = f"{site_name}: temporarily unavailable (HTTP {lookup.http_status})"
            else:
                title = f"{site_name}: couldn't tell -- the site didn't serve a readable profile page"
                metadata["blocked"] = "unreadable_page"
        return Finding(
            source=self._source_name(site_name),
            identifier=identifier,
            status=MatchStatus.ERROR,
            source_url=url,
            title=title,
            category=site.get("category", self.category),
            metadata=metadata,
        )

    def _crash_finding(self, site: Any, identifier: Identifier, exc: Exception) -> Finding:
        site = site if isinstance(site, dict) else {}
        site_name = site.get("name", "?")
        log.error("%s: check for %s failed unexpectedly: %s",
                  self._source_name(site_name), identifier.value, exc, exc_info=exc)
        return Finding(
            source=self._source_name(site_name),
            identifier=identifier,
            status=MatchStatus.ERROR,
            source_url=site.get("url", ""),
            title=f"{site_name}: check failed (internal error)",
            category=site.get("category", self.category),
            metadata={"outcome": LookupOutcome.UNKNOWN.value, "freshness": "unavailable",
                      "error": f"{type(exc).__name__}: {exc}"},
        )
