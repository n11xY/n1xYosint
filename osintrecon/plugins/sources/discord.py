"""Discord source module -- Discord has no public profile pages, so the
usual "does this page 404" approach is impossible there. Instead this uses
Discord's own username-availability check, the same endpoint their signup
form calls, which returns whether a username is already taken without
requiring login. Live-verified against both taken and available usernames
before shipping.

The endpoint is rate limited hard and its limit is shared ("x-ratelimit-
scope: shared"): checked 2026-11-06, one request answered 429 with
Retry-After 1005 s. So the module asks as little as it can:

- names Discord can't have (it allows 2-32 lowercase letters, digits, "_"
  and ".", no ".." -- the handle is lowercased first) are answered without
  a request;
- what each lookup concluded is cached -- "taken" for a day, "available" for
  six hours -- so repeated searches don't ask again;
- one request at a time, a few seconds apart, never retried within a lookup;
- a 429 pauses Discord for as long as Retry-After says (or a backoff), and
  the pause is kept in the cache database, so it holds across searches and
  processes. While paused nothing is sent and the finding says until when.

All of it is configurable under sources.discord (see config.py).
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any, ClassVar, Optional

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin
from osintrecon.plugins.sources.username_sites import SiteSettings

API_URL = "https://discord.com/api/v9/unique-username/username-attempt-unauthed"
USERNAME_RE = re.compile(r"(?!.*\.\.)[a-z0-9_.]{2,32}")


def _iso(epoch: Optional[float]) -> Optional[str]:
    if not epoch:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds")


def _readable(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


class DiscordPlugin(SourcePlugin):
    name: ClassVar[str] = "discord"
    category: ClassVar[str] = "social"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME}
    description: ClassVar[str] = "Checks Discord's own username-availability endpoint (no public profiles exist to scrape)."

    def __init__(self, config: dict, http):
        super().__init__(config, http)
        self.settings = SiteSettings.from_config(config)
        if self.settings.policy is not None and hasattr(http, "set_source_policy"):
            http.set_source_policy(self.name, self.settings.policy)

    def _cache(self):
        return getattr(self.http, "cache", None) if self.settings.cache_ttl is not None else None

    async def run(self, identifier: Identifier) -> list[Finding]:
        value = identifier.value.lower()
        if not USERNAME_RE.fullmatch(value):
            return []  # not a name Discord allows: no account, nothing to ask

        cache = self._cache()
        stored = cache.get_result(self.name, value) if cache is not None else None
        if stored is not None and not stored["stale"]:
            return self._from_store(identifier, stored)

        resp = await self.http.request(
            self.name, "POST", API_URL,
            json_body={"username": value},
            headers={"Content-Type": "application/json"},
            expected_statuses={400, 429},
        )

        if getattr(resp, "skipped", False):
            return [self._problem(identifier, "rate_limited", resp, sent=False)]
        if resp.error is not None:
            return [self._problem(identifier, "network_error", resp)]
        if resp.status == 429:
            return [self._problem(identifier, "rate_limited", resp)]
        if resp.status == 400:
            return []  # reserved or invalid -- can't be an account
        if resp.status != 200:
            return [self._problem(identifier, "unknown", resp)]

        data = resp.json() or {}
        if not isinstance(data, dict) or "taken" not in data:
            return [self._problem(identifier, "unknown", resp)]

        collected = getattr(resp, "fetched_at", None) or time.time()
        if not data["taken"]:
            if cache is not None and self.settings.negative_cache_ttl:
                cache.put_result(self.name, value, {"taken": False}, self.settings.negative_cache_ttl, collected)
            return []
        if cache is not None:
            cache.put_result(self.name, value, {"taken": True}, self.settings.cache_ttl, collected)  # type: ignore[arg-type]
        return [self._taken(identifier, value, collected, "direct", resp.evidence_path)]

    def _from_store(self, identifier: Identifier, stored: dict[str, Any]) -> list[Finding]:
        if not stored["payload"].get("taken"):
            return []
        return [self._taken(identifier, identifier.value.lower(), stored["collected_at"], "cache", None)]

    def _taken(self, identifier: Identifier, value: str, collected: float, origin: str,
               evidence_path: Optional[str]) -> Finding:
        return Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=API_URL,
            title=f"Discord username taken: {value}",
            category=self.category,
            metadata={
                "note": "Discord has no public profile pages; this only confirms the "
                        "username is registered, not which account owns it.",
                "outcome": "profile_found", "source_status": "SUCCESS", "data_origin": origin,
                "verification": "verified", "retry_eligible": False, "collected_at": _iso(collected),
                "cached": origin == "cache",
            },
            evidence_path=evidence_path,
        )

    def _problem(self, identifier: Identifier, outcome: str, resp, sent: bool = True) -> Finding:
        until = getattr(resp, "cooldown_until", None)
        metadata: dict[str, Any] = {"outcome": outcome, "freshness": "unavailable",
                                    "data_origin": "direct", "verification": "unverified",
                                    "retry_eligible": True, "request_sent": sent}
        if until:
            metadata["cooldown_until"] = _iso(until)
        if outcome == "rate_limited" and not sent:
            metadata.update(http_status=None, suppressed_by_cooldown=True, source_status="TEMPORARILY_UNAVAILABLE")
            title = "Discord: not checked -- paused until " + (_readable(until) if until else "later") + \
                    " after a rate limit"
        elif outcome == "rate_limited":
            metadata.update(http_status=429, source_status="RATE_LIMITED")
            title = "Discord: rate limited" + (f" -- paused until {_readable(until)}" if until else "")
        elif outcome == "network_error":
            metadata.update(error=resp.error, source_status="TEMPORARILY_UNAVAILABLE")
            title = "Discord: request failed"
        else:
            metadata.update(http_status=resp.status, source_status="TEMPORARILY_UNAVAILABLE")
            title = f"Discord: temporarily unavailable (HTTP {resp.status})"
        return Finding(source=self.name, identifier=identifier, status=MatchStatus.ERROR, source_url=API_URL,
                       title=title, category=self.category, metadata=metadata)
