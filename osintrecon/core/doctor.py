"""Diagnostic / setup-verification pass (`n1xyosint --doctor`).

Runs lightweight, read-only checks -- config loading, cache/evidence
filesystem paths, and DNS resolution for every enabled source's target
domain(s) -- so setup problems (a source that's unreachable from this
network, a bad config path, a forgotten API key) surface up front with a
clear checklist, instead of being discovered mid-scan as a wall of
per-identifier error rows.
"""
from __future__ import annotations

import asyncio
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from osintrecon.core.config import Config
from osintrecon.core.http_client import AsyncHttpClient
from osintrecon.plugins.registry import PluginRegistry

# Known primary API domain(s) for sources that don't hit a per-identifier
# site database. Kept here rather than on each plugin class so this stays a
# read-only diagnostic concern, not something plugin authors must remember.
PLUGIN_DOMAINS: dict[str, list[str]] = {
    "github": ["api.github.com"],
    "gitlab": ["gitlab.com"],
    "roblox": ["users.roblox.com"],
    "minecraft": ["api.mojang.com"],
    "bluesky": ["public.api.bsky.app"],
    "anilist": ["graphql.anilist.co"],
    "discord": ["discord.com"],
    "twitter_email": ["api.twitter.com"],
    "gravatar": ["www.gravatar.com"],
    "pastebin_search": ["psbdmp.ws"],
    "xposedornot": ["api.xposedornot.com"],
    "emailrep": ["emailrep.io"],
    "hibp": ["haveibeenpwned.com"],
    "twitch_api": ["id.twitch.tv", "api.twitch.tv"],
    "steam_api": ["api.steampowered.com"],
    "hunter_io": ["api.hunter.io"],
    "search_api": ["www.googleapis.com"],
    "twitter_api": ["api.twitter.com"],
    "abstractapi_phone": ["phonevalidation.abstractapi.com"],
    "github_commit_email": ["api.github.com"],
    "wayback": ["web.archive.org"],
    "telegram_api": ["api.telegram.org"],
    "youtube_api": ["www.googleapis.com"],
}

DNS_CONCURRENCY = 20
DNS_TIMEOUT = 5


@dataclass
class DomainCheck:
    domain: str
    ok: bool
    error: str = ""


@dataclass
class SourceCheck:
    name: str
    ready: bool             # would actually run in a real scan
    configured: bool
    reason: str = ""        # why it's not ready, if it isn't
    domains: list[DomainCheck] = field(default_factory=list)


@dataclass
class DoctorReport:
    config_loaded_ok: bool = True
    config_error: str = ""
    cache_writable: bool = True
    cache_error: str = ""
    evidence_writable: bool = True
    evidence_error: str = ""
    evidence_checked: bool = False
    proxy: str | None = None
    proxy_reachable: Optional[bool] = None  # None = no proxy configured, so not checked
    proxy_error: str = ""
    sources: list[SourceCheck] = field(default_factory=list)
    elapsed: float = 0.0


def _extract_domain(url: str) -> str:
    return urlparse(url.replace("{}", "x")).netloc


def _parse_proxy_host_port(proxy_url: str) -> tuple[str, int] | None:
    parsed = urlparse(proxy_url)
    if not parsed.hostname or not parsed.port:
        return None
    return parsed.hostname, parsed.port


async def _check_proxy_reachable(proxy_url: str) -> tuple[bool, str]:
    """A raw TCP connect to the proxy's host:port -- confirms something is
    actually listening there, without doing a full SOCKS/HTTP handshake.
    Catches the single most common proxy misconfiguration: Tor (or whatever
    the proxy is) simply isn't running."""
    hostport = _parse_proxy_host_port(proxy_url)
    if hostport is None:
        return False, f"could not parse host/port from {proxy_url!r}"
    host, port = hostport
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5)
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        return True, ""
    except (OSError, asyncio.TimeoutError) as exc:
        return False, str(exc) or type(exc).__name__


def _check_path_writable(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".doctor_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True, ""
    except OSError as exc:
        return False, str(exc)


async def _dns_check(domain: str, sem: asyncio.Semaphore) -> DomainCheck:
    loop = asyncio.get_running_loop()
    async with sem:
        try:
            await asyncio.wait_for(
                loop.run_in_executor(None, socket.getaddrinfo, domain, 443),
                timeout=DNS_TIMEOUT,
            )
            return DomainCheck(domain=domain, ok=True)
        except (OSError, asyncio.TimeoutError) as exc:
            return DomainCheck(domain=domain, ok=False, error=str(exc) or "resolution failed")


async def run_doctor(config: Config) -> DoctorReport:
    start = time.time()
    report = DoctorReport()

    cache_path = Path(config.get("cache.path", "~/.cache/n1xYosint/cache.sqlite3")).expanduser().parent
    report.cache_writable, report.cache_error = _check_path_writable(cache_path)

    if config.get("evidence.save_raw", False):
        report.evidence_checked = True
        ev_path = Path(config.get("evidence.path", "~/.local/share/n1xYosint/evidence")).expanduser()
        report.evidence_writable, report.evidence_error = _check_path_writable(ev_path)

    report.proxy = config.get("proxy")
    if report.proxy:
        report.proxy_reachable, report.proxy_error = await _check_proxy_reachable(report.proxy)

    async with AsyncHttpClient() as http:
        registry = PluginRegistry(config, http).discover()
        all_classes = registry.all_known()
        ready_instances = {p.name: p for p in registry.instantiate_enabled()}

        sem = asyncio.Semaphore(DNS_CONCURRENCY)
        source_checks: list[SourceCheck] = []
        dns_jobs: list[tuple[SourceCheck, asyncio.Task]] = []

        for cls in all_classes:
            source_cfg = config.source_config(cls.name)
            probe = cls(source_cfg, http)
            configured = probe.is_configured()
            ready = cls.name in ready_instances

            reason = ""
            if not ready:
                reason = "not configured (missing key/setup)" if not configured else "disabled in config"

            check = SourceCheck(name=cls.name, ready=ready, configured=configured, reason=reason)
            source_checks.append(check)

            if not ready:
                continue

            if cls.name == "username_sites":
                sites_db = getattr(probe, "sites", [])
                seen: set[str] = set()
                for site in sites_db:
                    domain = _extract_domain(site.get("url", ""))
                    if domain and domain not in seen:
                        seen.add(domain)
                        dns_jobs.append((check, asyncio.create_task(_dns_check(domain, sem))))
            else:
                for domain in PLUGIN_DOMAINS.get(cls.name, []):
                    dns_jobs.append((check, asyncio.create_task(_dns_check(domain, sem))))

        for check, task in dns_jobs:
            check.domains.append(await task)

        report.sources = source_checks

    report.elapsed = time.time() - start
    return report
