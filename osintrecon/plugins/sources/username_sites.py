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

An entry can also name "restrictions": pieces of a response that mean the
site declined to answer, each with its kind -- "rate_limited" (Instagram's
"Please wait a few minutes", "feedback_required"), "auth_required"
("require_login"), "challenge_required" (challenge or checkpoint) or
"error_page" (the generic error shell Instagram serves to clients it won't
show a profile to). A restricted response is never a result: it reads as
rate limited or access restricted, pauses the site like a 429 does when the
site has a request policy, and the finding says which restriction it was.

Every finding also carries a uniform status for the source:
metadata["source_status"] (SUCCESS, PARTIAL, RATE_LIMITED, AUTH_REQUIRED,
CHALLENGE_REQUIRED, TEMPORARILY_UNAVAILABLE or ERROR), "data_origin"
(direct, cache or stale_cache), "verification" (verified or unverified) and
"retry_eligible", plus "cooldown_until" when a pause is known. "Not found"
produces no finding, so a restriction can never be shown as one.

A third-party mirror of a platform ("mirror_of": "Instagram") is labelled as
such: its findings carry data_origin "third_party" and verification
"unverified", since the answer is the mirror's, not the platform's. A site
that can only ever confirm an account ("found_only": true -- a mirror that
shows a profile when there is one but nothing telling when there isn't)
reports found profiles only and stays silent otherwise, instead of adding a
"couldn't tell" row to every search. "confirmed": true shows a found profile
as CONFIRMED where the markers are strong enough to trust (the Instagram
entry, whose markers are the profile header only a real account has).
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

from osintrecon.core import platform_urls, profile_extract
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


def _headers_for(site: dict) -> Optional[dict[str, str]]:
    """The browser headers above, unless the entry says "browser_headers":
    false -- for sites whose bot check challenges a browser User-Agent that
    doesn't behave like a browser (Last.fm serves a "Client Challenge" page
    to it for every username) while answering the client's own honest one.
    An entry's own "headers" are added on top (YouTube gets the cookie that
    declines its EU consent page, which otherwise answers every name)."""
    base = _BROWSER_HEADERS if site.get("browser_headers", True) else None
    extra = site.get("headers")
    if not extra:
        return base
    return {**(base or {}), **extra}


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
    restriction: Optional[str] = None
    # What the found page says about its owner (core/profile_extract.py).
    profile: Optional[dict[str, Any]] = None
    # The URL that answered, when a fallback did rather than the entry's own.
    checked_url: str = ""


def _profile_of(resp, site: dict, value: str, url: str) -> Optional[dict[str, Any]]:
    """What the found page says about its owner, or None. Never fails the
    lookup: a page it can't read just carries no profile."""
    try:
        headers = getattr(resp, "headers", None) or {}
        content_type = next((v for k, v in headers.items() if k.lower() == "content-type"), "")
        profile = profile_extract.extract(
            resp.text or "", getattr(resp, "url", "") or url, site["name"], value,
            link_hub=bool(site.get("link_hub")), content_type=content_type,
        )
    except Exception as exc:  # noqa: BLE001 -- extraction is an extra
        log.debug("profile extraction failed for %s: %s", site["name"], exc)
        return None
    return profile or None


# How many leads one found profile may add to the next enrichment round:
# a link hub can list dozens of links, and each discovered username is a
# full scan at --depth 2.
_MAX_DISCOVERED_USERNAMES = 3
_MAX_DISCOVERED_URLS = 3


def _discovered_from_profile(profile: dict[str, Any], identifier: Identifier) -> list[Identifier]:
    out: list[Identifier] = []
    usernames = {identifier.value.lower()}
    for account in profile.get("linked_accounts") or []:
        name = str(account.get("username") or "")
        if name and name.lower() not in usernames and len(usernames) <= _MAX_DISCOVERED_USERNAMES:
            usernames.add(name.lower())
            out.append(Identifier(value=name, type=IdentifierType.USERNAME))
    # Personal websites only: a link to another platform's profile is
    # already a linked account above.
    domains: set[str] = set()
    for url in profile.get("links") or []:
        if len(domains) >= _MAX_DISCOVERED_URLS or not platform_urls.personal_site(url):
            continue
        domain = platform_urls.site_key(url)
        if domain not in domains:
            domains.add(domain)
            out.append(Identifier(value=url, type=IdentifierType.URL))
    return out


# A fallback page is asked only when the entry's own page gave no answer
# either way (or didn't load): never to second-guess a "not found", and not
# while the source is cooling down or blocked.
_FALLBACK_ON = (LookupOutcome.UNKNOWN, LookupOutcome.NETWORK_ERROR)

# Why a profile seen before couldn't be re-checked, in words.
_STALE_REASON = {
    LookupOutcome.UNKNOWN: "the page didn't show the profile either way",
    LookupOutcome.NETWORK_ERROR: "the request failed",
    LookupOutcome.RATE_LIMITED: "rate limited",
    LookupOutcome.ACCESS_RESTRICTED: "access restricted",
}


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


def _strict(site: dict) -> bool:
    """Whether the entry names what a real profile page must contain
    ("found_text" literals, "found_pattern" regex): the strict rules."""
    return bool(site.get("found_text") or site.get("found_pattern"))


def _has_profile_markers(resp, site: dict, username: str) -> bool:
    if resp.status >= 400:
        return False
    markers = _profile_markers(site, username)
    pattern = site.get("found_pattern")
    if not markers and not pattern:
        return False
    text = resp.text or ""
    # Also matched against the entity-decoded page: sites write parts of a
    # title as HTML entities (Instagram has used "&#064;" for "@").
    decoded = html.unescape(text)
    if not all(marker in text or marker in decoded for marker in markers):
        return False
    if pattern:
        # "{}" stands for the username, as in found_text.
        compiled = pattern.replace("{}", re.escape(username))
        return bool(re.search(compiled, text) or re.search(compiled, decoded))
    return True


def _redirected_to_not_found(resp, site: dict) -> bool:
    """Some sites answer a free name by redirecting somewhere telling: Bandcamp
    sends <name>.bandcamp.com to its sign-up page for that subdomain."""
    marker = site.get("not_found_url")
    return bool(marker) and marker in (getattr(resp, "url", "") or "")


def _has_not_found_signal(resp, site: dict) -> bool:
    if _redirected_to_not_found(resp, site):
        return True
    not_found_text = site.get("not_found_text")
    if not_found_text and not_found_text in (resp.text or ""):
        return True
    return "not_found_status" in site and resp.status == site["not_found_status"]


def _is_found(resp, site: dict, username: str = "") -> bool:
    check_type = site.get("check", "status")
    if check_type == "status":
        return resp.status == site.get("found_status", 200)
    if _strict(site):
        return _has_profile_markers(resp, site, username)
    not_found_text = site.get("not_found_text", "")
    return resp.status < 400 and bool(not_found_text) and not_found_text not in resp.text


RESTRICTION_KINDS = ("rate_limited", "auth_required", "challenge_required", "error_page")


def _restriction(resp, site: dict) -> Optional[str]:
    """The kind of restriction the response body shows, or None. When several
    markers match, the most specific wins in RESTRICTION_KINDS order: Instagram
    answers a rate limit as 401 with both "Please wait a few minutes" and
    "require_login":true, and waiting -- not signing in -- is what it asks."""
    markers = site.get("restrictions") or {}
    text = resp.text or ""
    found = {kind if kind in RESTRICTION_KINDS else "error_page"
             for marker, kind in markers.items() if marker and marker in text}
    return next((kind for kind in RESTRICTION_KINDS if kind in found), None)


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
    if _redirected_to_not_found(resp, site):
        # Before any page check: the page at the sign-up URL may itself be a
        # bot challenge, but the redirect alone already answered.
        return LookupOutcome.NOT_FOUND
    if _strict(site) and _has_profile_markers(resp, site, username):
        # The strict markers include the username itself, so they win over
        # any restriction text that page code might also happen to contain.
        return LookupOutcome.PROFILE_FOUND
    restriction = _restriction(resp, site)
    if restriction == "rate_limited":
        return LookupOutcome.RATE_LIMITED
    if restriction is not None:
        return LookupOutcome.ACCESS_RESTRICTED
    if _strict(site):
        if _has_not_found_signal(resp, site):
            return LookupOutcome.NOT_FOUND
        if resp.status in (401, 403):
            return LookupOutcome.ACCESS_RESTRICTED
        # An error shell, a bot challenge, a page served identically for
        # every username: absence of the profile marker alone is not
        # evidence the account doesn't exist.
        return LookupOutcome.UNKNOWN
    if resp.status in (401, 403) and resp.status != site.get("not_found_status"):
        # A bot challenge or a block (Cloudflare answers 403) says nothing
        # about the account; read as "not found" it hid real accounts on
        # every status-only entry behind such a wall.
        return LookupOutcome.ACCESS_RESTRICTED
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
        # Adult sites ("nsfw": true) are opt-in: looking a username up on them
        # should be a deliberate choice by whoever runs the search, not
        # something every search does (include_adult, or --include-adult).
        if not config.get("include_adult", False):
            self.sites = [site for site in self.sites if not site.get("nsfw")]
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

    def planned_checks(self, identifier: Identifier) -> list[str]:
        return [self._source_name(site["name"]) for site in self.sites
                if self.site_settings.get(site["name"], _DEFAULT_SETTINGS).enabled]

    async def _check_and_report(self, site: dict, identifier: Identifier, decoy: str) -> Finding | None:
        try:
            result = await self._check_site(site, identifier, decoy)
        except Exception as exc:  # noqa: BLE001 -- turned into this site's error finding
            result = self._crash_finding(site, identifier, exc)
        if self.report_check is not None and self.site_settings.get(site["name"], _DEFAULT_SETTINGS).enabled:
            self.report_check(self._source_name(site["name"]), identifier, [result] if result is not None else [])
        return result

    async def run(self, identifier: Identifier) -> list[Finding]:
        # One decoy per run (not per site) -- it doesn't need to be
        # per-site-unique, only unpredictable and unregistered, and reusing
        # it keeps this to one extra random value instead of 90+.
        decoy = _decoy_username()
        # return_exceptions: one site's check failing in an unexpected way
        # must cost that site's result only, not the other 90+.
        results = await asyncio.gather(
            *(self._check_and_report(site, identifier, decoy) for site in self.sites), return_exceptions=True
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
        # Entries that check a JSON API name the page a person would open
        # in "profile_url"; that's the link a finding shows.
        profile_url = site["profile_url"].format(quote(value, safe="")) if site.get("profile_url") else url
        source_name = self._source_name(site["name"])
        lookup = await self._shared_lookup(
            source_name, value, lambda: self._lookup(site, source_name, value, url, settings, decoy)
        )
        return self._finding(site, identifier, url, lookup, profile_url)

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
        """The entry's own check, then -- only when that page said nothing
        either way -- each of its "fallbacks" in turn: other pages for the
        same platform (a second viewer for Instagram), each with its own URL
        and markers, sharing the entry's request policy."""
        lookup = await self._lookup_once(site, source_name, value, url, settings, decoy)
        for fallback in site.get("fallbacks") or []:
            reason = lookup.stale_reason if lookup.freshness == "stale" else lookup.outcome
            if lookup.freshness not in ("unavailable", "stale") or reason not in _FALLBACK_ON:
                break
            fb_site = {key: val for key, val in site.items() if key != "fallbacks"}
            fb_site.update(fallback)
            fb_url = fb_site["url"].format(quote(value, safe=""))
            answer = await self._lookup_once(fb_site, source_name, value, fb_url, settings, decoy)
            if answer.freshness not in ("unavailable", "stale"):
                answer.checked_url = fb_url
                return answer
        return lookup

    async def _lookup_once(self, site: dict, source_name: str, value: str, url: str,
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
        resp = await self.http.get(source_name, url, headers=_headers_for(site), expected_statuses={404}, **get_kwargs)
        outcome = _classify(resp, site, value)
        collected_at = getattr(resp, "fetched_at", None) or time.time()

        if outcome in _UNAVAILABLE:
            restriction = None if getattr(resp, "skipped", False) else _restriction(resp, site)
            body_rate_limit = outcome is LookupOutcome.RATE_LIMITED and resp.status != 429
            resp_cooldown = getattr(resp, "cooldown_until", None)
            if (outcome is LookupOutcome.ACCESS_RESTRICTED or body_rate_limit) and settings.policy is not None:
                # A login wall, challenge or "please wait" is the site
                # declining to answer this network right now. Asking again
                # later in this run (or the next one) gets the same, so back
                # off as for a 429 (the client does that itself only for a
                # real 429 status).
                resp_cooldown = self.http.start_cooldown(source_name)
            if (stored is not None and stored["payload"].get("outcome") == LookupOutcome.PROFILE_FOUND.value
                    and time.time() <= stored["expires_at"] + settings.stale_ttl):
                lookup = self._lookup_from_store(stored, "stale")
                lookup.stale_reason = outcome
                return lookup
            return _Lookup(
                outcome=outcome, freshness="unavailable",
                http_status=resp.status or None, collected_at=collected_at,
                final_url=getattr(resp, "url", "") or "", error=getattr(resp, "error", None),
                cooldown_until=resp_cooldown,
                request_sent=not getattr(resp, "skipped", False),
                restriction=restriction,
            )

        if outcome is LookupOutcome.NOT_FOUND:
            if cache is not None and settings.negative_cache_ttl:
                cache.put_result(source_name, value, {"outcome": outcome.value, "http_status": resp.status},
                                 settings.negative_cache_ttl, collected_at)
            return _Lookup(outcome=outcome, freshness="fresh", http_status=resp.status, collected_at=collected_at)

        status = (MatchStatus.CONFIRMED if site.get("check", "status") == "status" or site.get("confirmed")
                  else MatchStatus.PROBABLE)
        if settings.decoy_verification_enabled:
            decoy_check, decoy_failed = await self._decoy_check(site, source_name, decoy, get_kwargs)
            if decoy_failed:
                status = MatchStatus.UNCERTAIN
        else:
            decoy_check = "disabled for this site"

        profile = _profile_of(resp, site, value, url)
        expires_at = None
        # A failed decoy check means this site's signal can't be trusted
        # right now -- not something to keep.
        if cache is not None and status is not MatchStatus.UNCERTAIN:
            cache.put_result(
                source_name, value,
                {"outcome": outcome.value, "status": status.value, "http_status": resp.status, "decoy_check": decoy_check,
                 "profile": profile},
                settings.cache_ttl, collected_at,  # type: ignore[arg-type]
            )
            expires_at = collected_at + settings.cache_ttl  # type: ignore[operator]
        return _Lookup(
            outcome=outcome, freshness="cached" if getattr(resp, "cached", False) else "fresh",
            status=status, http_status=resp.status, collected_at=collected_at, expires_at=expires_at,
            decoy_check=decoy_check, final_url=getattr(resp, "url", "") or "",
            evidence_path=getattr(resp, "evidence_path", None), profile=profile,
        )

    async def _decoy_check(self, site: dict, source_name: str, decoy: str,
                           get_kwargs: dict[str, Any]) -> tuple[str, bool]:
        """(metadata note, whether the decoy also looked found)."""
        decoy_url = site["url"].format(quote(decoy, safe=""))
        await asyncio.sleep(random.uniform(*_JITTER_SECONDS))
        decoy_resp = await self.http.get(source_name, decoy_url, headers=_headers_for(site),
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
            profile=payload.get("profile") or None,
        )

    def _finding(self, site: dict, identifier: Identifier, url: str, lookup: _Lookup,
                 profile_url: Optional[str] = None) -> Finding | None:
        shown_url = profile_url or url
        if lookup.freshness == "unavailable":
            if site.get("found_only"):
                return None
            return self._unavailable_finding(site, identifier, shown_url, lookup)
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
        if lookup.checked_url:
            url = lookup.checked_url
        if shown_url != url:
            metadata["checked_url"] = url
        if lookup.expires_at:
            metadata["cache_expires_at"] = _iso(lookup.expires_at)
        metadata.update(
            source_status="PARTIAL" if lookup.freshness == "stale" else "SUCCESS",
            data_origin={"fresh": "direct", "cached": "cache", "stale": "stale_cache"}[lookup.freshness],
            retry_eligible=lookup.freshness == "stale",
        )

        status = lookup.status or MatchStatus.PROBABLE
        title = f"{site_name} profile found for '{identifier.value}'"
        if lookup.freshness == "stale":
            # Seen before, can't be re-checked now: still worth showing, but
            # only as something for a person to confirm.
            reason = lookup.stale_reason or LookupOutcome.UNKNOWN
            status = MatchStatus.UNCERTAIN
            title = (
                f"{site_name}: profile seen on {(_iso(lookup.collected_at) or '')[:10]} for '{identifier.value}' "
                f"(stale -- can't be re-checked right now: {_STALE_REASON.get(reason, reason.value.replace('_', ' '))})"
            )
            metadata["unavailable_reason"] = reason.value
        elif status is MatchStatus.UNCERTAIN:
            title = (
                f"{site_name}: possible match for '{identifier.value}' "
                "(unverified -- this site's found/not-found signal looked unreliable this run)"
            )

        metadata["verification"] = (
            "verified" if status in (MatchStatus.CONFIRMED, MatchStatus.PROBABLE) else "unverified"
        )
        discovered: list[Identifier] = []
        if lookup.profile:
            # name, bio, location, avatar_url, links, linked_accounts: the
            # keys the dedicated plugins use, so correlation reads them alike.
            metadata.update(lookup.profile)
            discovered = _discovered_from_profile(lookup.profile, identifier)
        if site.get("mirror_of"):
            # Where the answer came from stays on record even when the entry
            # is trusted enough to show as the platform itself.
            metadata.update(mirror_of=site["mirror_of"], data_origin="third_party", checked_url=url)
            if status is not MatchStatus.CONFIRMED:
                metadata["verification"] = "unverified"
        return Finding(
            source=self._source_name(site_name),
            identifier=identifier,
            status=status,
            source_url=shown_url,
            title=title,
            category=site.get("category", self.category),
            metadata=metadata,
            discovered_identifiers=discovered,
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
        elif lookup.outcome is LookupOutcome.RATE_LIMITED and lookup.restriction == "rate_limited":
            metadata["http_status"] = lookup.http_status
            metadata["request_sent"] = True
            metadata["cooldown_until"] = _iso(lookup.cooldown_until)
            metadata["blocked"] = "rate_limited"
            title = f"{site_name}: rate limited (asked to wait)"
            if lookup.cooldown_until:
                title += f" -- paused until {_readable_time(lookup.cooldown_until)}"
        elif lookup.outcome is LookupOutcome.RATE_LIMITED:
            metadata["http_status"] = 429
            metadata["request_sent"] = lookup.request_sent
            metadata["cooldown_until"] = _iso(lookup.cooldown_until)
            if not lookup.request_sent and lookup.cooldown_until:
                # Nothing was sent: no HTTP status to report, and the pause
                # may have followed a sign-in wall or error page, not a 429.
                metadata["http_status"] = None
                metadata["suppressed_by_cooldown"] = True
                title = (
                    f"{site_name}: not checked -- paused until {_readable_time(lookup.cooldown_until)} "
                    "after a rate limit, sign-in wall or error page"
                )
            else:
                title = f"{site_name}: temporarily unavailable (HTTP 429)"
                if lookup.cooldown_until:
                    title += f" -- paused until {_readable_time(lookup.cooldown_until)}"
        elif lookup.outcome is LookupOutcome.ACCESS_RESTRICTED and lookup.restriction:
            metadata["http_status"] = lookup.http_status
            metadata["blocked"] = lookup.restriction
            metadata["cooldown_until"] = _iso(lookup.cooldown_until)
            title = {
                "auth_required": f"{site_name}: requires sign-in from this network -- open the link to check by hand",
                "challenge_required": f"{site_name}: answered with a security challenge -- open the link to check by hand",
                "error_page": f"{site_name}: served its error page instead of the profile -- open the link to check by hand",
            }[lookup.restriction]
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
            elif site.get("unanswered") == "not_cached" and lookup.outcome is LookupOutcome.UNKNOWN:
                # Viewer sites show only accounts they already have; opening
                # the profile once in a browser makes them fetch it. The row
                # links to the viewer page, so that's one click.
                value = identifier.value.lower() if site.get("lowercase") else identifier.value
                url = site["url"].format(quote(value, safe=""))
                title = (f"{site_name}: couldn't tell -- the viewer sites don't have this account yet; "
                         "open the link once, then search again")
                metadata["blocked"] = "not_cached"
            else:
                title = f"{site_name}: couldn't tell -- the site didn't serve a readable profile page"
                metadata["blocked"] = "unreadable_page"
        metadata.update(
            source_status=_unavailable_status(lookup, metadata.get("blocked")),
            data_origin="direct", verification="unverified", retry_eligible=True,
        )
        if lookup.cooldown_until and not metadata.get("cooldown_until"):
            metadata["cooldown_until"] = _iso(lookup.cooldown_until)
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
                      "error": f"{type(exc).__name__}: {exc}", "source_status": "ERROR",
                      "data_origin": "direct", "verification": "unverified", "retry_eligible": True},
        )


def _unavailable_status(lookup: _Lookup, blocked: Optional[str]) -> str:
    """The uniform source status for a lookup that produced no answer."""
    if lookup.outcome is LookupOutcome.RATE_LIMITED:
        # A pause of ours (nothing sent) is the site being unavailable for
        # now; only an actual answer from the site is a rate limit.
        return "RATE_LIMITED" if lookup.request_sent else "TEMPORARILY_UNAVAILABLE"
    if lookup.outcome is LookupOutcome.ACCESS_RESTRICTED:
        if blocked in ("login_wall", "auth_required"):
            return "AUTH_REQUIRED"
        if blocked == "challenge_required":
            return "CHALLENGE_REQUIRED"
    return "TEMPORARILY_UNAVAILABLE"
