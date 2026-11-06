"""Is each site in the username database still answering correctly?

Asks every site about one random username that nobody has registered:

* a site that says the account exists is broken -- it now answers "found"
  for everyone (a sign-in wall, a bot challenge page, a changed 404), so
  every real search would report a false hit there;
* a site that can't be asked (blocked, rate-limited, down, unreadable) is
  listed with the reason, as it is in a search;
* a site that says "not found" is healthy.

What it can't tell: a site that has started saying "not found" for real
accounts too. Catching that needs a known account per site, which the
database doesn't keep.

    python -m osintrecon.core.site_health [--config FILE] [--out report.json]

Meant to run on a schedule from the same network the searches run from:
blocking depends on the caller's IP.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys
import time
from typing import Optional

from osintrecon.core.config import Config
from osintrecon.core.http_client import AsyncHttpClient
from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus

_FOUND = (MatchStatus.CONFIRMED, MatchStatus.PROBABLE, MatchStatus.UNCERTAIN)


def summarize(site_names: list[str], findings: list[Finding], disabled: set[str], username: str) -> dict:
    """The report, from the sites asked and what the check returned."""
    false_positive: list[dict] = []
    unavailable: list[dict] = []
    answered: set[str] = set()
    for f in findings:
        site = f.source.split(":", 1)[1] if f.source.startswith("username_sites:") else f.source
        if site in answered:
            continue
        answered.add(site)
        meta = f.metadata or {}
        if f.status in _FOUND:
            false_positive.append({"site": site, "status": f.status.value, "url": f.source_url})
        elif f.status is MatchStatus.ERROR:
            unavailable.append({"site": site, "outcome": meta.get("outcome") or "error",
                                "http_status": meta.get("http_status"), "detail": f.title})
    checked = [s for s in site_names if s not in disabled]
    return {
        "checked_at": int(time.time()),
        "username": username,
        "sites": len(checked),
        "disabled": sorted(disabled),
        "healthy": len(checked) - len(false_positive) - len(unavailable),
        "false_positive": sorted(false_positive, key=lambda r: r["site"].lower()),
        "unavailable": sorted(unavailable, key=lambda r: r["site"].lower()),
    }


async def check(config: Config, include_adult: bool = True) -> dict:
    from osintrecon.plugins.sources.username_sites import _DEFAULT_SETTINGS, UsernameSitesPlugin

    site_config = dict(config.source_config("username_sites"))
    site_config["include_adult"] = include_adult
    username = "zq" + secrets.token_hex(5)
    async with AsyncHttpClient(
        timeout_seconds=config.get("timeout_seconds", 10),
        retries=config.get("retries", 2),
        user_agent=config.get("user_agent"),
        proxy=config.get("proxy"),
        cache=None,  # a stored page would hide what the site says today
    ) as http:
        plugin = UsernameSitesPlugin(site_config, http)
        disabled = {s["name"] for s in plugin.sites
                    if not plugin.site_settings.get(s["name"], _DEFAULT_SETTINGS).enabled}
        findings = await plugin.run(Identifier(value=username, type=IdentifierType.USERNAME))
    return summarize([s["name"] for s in plugin.sites], findings, disabled, username)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("-c", "--config", help="YAML/JSON config file")
    parser.add_argument("--out", help="Write the JSON report here")
    parser.add_argument("--no-adult", action="store_true", help="Skip the adult sites")
    parser.add_argument("--disable", action="append", default=[], metavar="SITE",
                        help="Skip a site (the way a deployment turns it off), repeatable")
    args = parser.parse_args(argv)

    config = Config.load(args.config)
    for site in args.disable:
        config.data.setdefault("sources", {}).setdefault("username_sites", {}) \
            .setdefault("site_settings", {}).setdefault(site, {})["enabled"] = False
    report = asyncio.run(check(config, include_adult=not args.no_adult))
    text = json.dumps(report, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    print(f"{report['healthy']}/{report['sites']} healthy, {len(report['false_positive'])} answering 'found' "
          f"for a random name, {len(report['unavailable'])} unavailable", file=sys.stderr)
    if not args.out:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
