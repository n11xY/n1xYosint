"""Async HTTP client wrapper around aiohttp.

Provides: configurable timeouts, bounded retries with exponential backoff,
per-source concurrency limiting (semaphores), proxy support, custom headers /
user-agent, transparent response caching, and evidence (raw response) capture.

Every source gets the client-wide defaults unless a plugin registers a
SourcePolicy for it (see username_sites' per-site settings), which can set
its own concurrency, timeout, retry count, minimum spacing between requests
and 429 cooldown. For such a source, cooldown and spacing are kept in the
cache database rather than only in memory, so they hold across runs -- each
web search is a run of its own, and without this every new search would
open with a fresh request to a site that rate-limited the last one seconds
ago.

On an HTTP 429 the client never retries straight away: it retries only when
Retry-After (seconds or an HTTP-date) asks for a short wait, and otherwise
puts the source on cooldown -- for as long as Retry-After says, or for a
bounded, jittered exponential backoff when it says nothing -- and answers
every request to that source with a synthetic 429 (nothing sent) until the
cooldown ends.

Identical GET/HEAD requests that are in flight at the same moment share one
network request (single-flight), and per-source counters (requests, 429s,
cache hits, duplicates avoided, response time, queue length, cooldown) are
kept for monitoring.
"""
from __future__ import annotations

import asyncio
import email.utils
import json
import math
import os
import random
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiohttp

from osintrecon.core.cache import ResponseCache
from osintrecon.core.logging_setup import get_logger
from osintrecon.core.models import RunStats

log = get_logger("http")

# Stored alongside a cached response's real headers (never sent anywhere).
_FINAL_URL_HEADER = "X-N1xyosint-Final-Url"

# A 429 is retried only when Retry-After asks for a wait no longer than this.
_MAX_RETRY_AFTER_WAIT = 10
# Base cooldown for a source that sent a 429 without a usable Retry-After;
# it doubles with each consecutive 429, up to the cap below.
_DEFAULT_429_COOLDOWN = 60
_DEFAULT_MAX_COOLDOWN = 3600


def _parse_retry_after(value: Optional[str], now: Optional[float] = None) -> Optional[float]:
    """Seconds to wait, from a Retry-After header in either of its two
    forms: delay-seconds ("120") or an HTTP-date ("Wed, 21 Oct 2026
    07:28:00 GMT"). A date in the past means "now" (0). None when the
    header is missing or unusable."""
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = max(0.0, when.timestamp() - (time.time() if now is None else now))
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return seconds


def _iso(epoch: Optional[float]) -> Optional[str]:
    if not epoch:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds")


@dataclass
class SourcePolicy:
    """Request handling for one source, overriding the client-wide defaults.

    None means "use the client default" for max_concurrency, request_timeout
    and max_retries. persist_state keeps cooldown and spacing in the cache
    database (when there is one) so they hold across runs and processes."""

    max_concurrency: Optional[int] = None
    request_timeout: Optional[float] = None
    min_interval: float = 0.0
    max_retries: Optional[int] = None
    cooldown: float = _DEFAULT_429_COOLDOWN
    max_cooldown: float = _DEFAULT_MAX_COOLDOWN
    max_retry_wait: float = _MAX_RETRY_AFTER_WAIT
    persist_state: bool = True


_DEFAULT_POLICY = SourcePolicy(persist_state=False)


@dataclass
class SourceMetrics:
    """Counters for one source over one client's lifetime (one run)."""

    requests_sent: int = 0          # actual network requests, retries included
    responses: int = 0              # any HTTP status received
    successful: int = 0             # status < 400
    rate_limited: int = 0           # HTTP 429 received
    network_errors: int = 0         # timeouts, connection failures
    cache_hits: int = 0             # answered from a cache, nothing sent
    duplicates_prevented: int = 0   # joined an identical in-flight request
    cooldown_skips: int = 0         # not sent because the source was cooling down
    queued: int = 0                 # waiting for a concurrency slot or spacing right now
    peak_queued: int = 0
    total_response_seconds: float = 0.0
    cooldown_until: Optional[float] = None

    @property
    def avg_response_ms(self) -> Optional[float]:
        if not self.responses:
            return None
        return round(self.total_response_seconds / self.responses * 1000, 1)

    def snapshot(self) -> dict[str, Any]:
        active = self.cooldown_until if self.cooldown_until and self.cooldown_until > time.time() else None
        return {
            "requests_sent": self.requests_sent,
            "responses": self.responses,
            "successful": self.successful,
            "rate_limited": self.rate_limited,
            "network_errors": self.network_errors,
            "cache_hits": self.cache_hits,
            "duplicates_prevented": self.duplicates_prevented,
            "cooldown_skips": self.cooldown_skips,
            "queued": self.queued,
            "peak_queued": self.peak_queued,
            "avg_response_ms": self.avg_response_ms,
            "cooling_down": active is not None,
            "cooldown_until": _iso(active),
        }


@dataclass
class HttpResponse:
    status: int
    text: str
    headers: dict[str, str]
    url: str
    ok: bool
    cached: bool = False
    error: Optional[str] = None
    json_data: Any = None
    evidence_path: Optional[str] = None
    # Raw response bytes, for binary content (e.g. avatar images for
    # avatar_correlation.py) that .text's UTF-8 decode would corrupt.
    # Reconstructed from the cached text on a cache hit -- fine for text
    # responses, not meaningful for binary ones, but binary fetches always
    # pass allow_cache=False (see avatar_correlation.py) so that path is
    # never actually hit for them.
    content: bytes = b""
    # When the response was actually received (epoch seconds) -- the
    # original time for a cache hit, not the time it was read back.
    fetched_at: Optional[float] = None
    # Set on a 429 (real or synthetic) that put or kept the source on
    # cooldown: when it may be asked again (epoch seconds).
    cooldown_until: Optional[float] = None
    # True when nothing was sent because the source is cooling down.
    skipped: bool = False

    def json(self) -> Any:
        if self.json_data is not None:
            return self.json_data
        try:
            self.json_data = json.loads(self.text)
        except (json.JSONDecodeError, TypeError):
            self.json_data = None
        return self.json_data


class AsyncHttpClient:
    def __init__(
        self,
        *,
        timeout_seconds: float = 10,
        retries: int = 2,
        retry_backoff: float = 1.5,
        user_agent: str = "n1xYosint/0.1",
        proxy: Optional[str] = None,
        default_headers: Optional[dict[str, str]] = None,
        cache: Optional[ResponseCache] = None,
        rate_limit_per_source: int = 5,
        stats: Optional[RunStats] = None,
        save_evidence: bool = False,
        evidence_dir: Optional[str] = None,
    ):
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.retries = retries
        self.retry_backoff = retry_backoff
        self.user_agent = user_agent
        self.proxy = proxy
        self.default_headers = default_headers or {}
        self.cache = cache
        self.rate_limit_per_source = rate_limit_per_source
        self.stats = stats
        self.save_evidence = save_evidence
        self.evidence_dir = Path(evidence_dir).expanduser() if evidence_dir else None
        self._session: Optional[aiohttp.ClientSession] = None
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._policies: dict[str, SourcePolicy] = {}
        self._metrics: dict[str, SourceMetrics] = {}
        # In-memory state, used for every source; a source with a persisted
        # policy also reads/writes the same values in the cache database.
        # All times are epoch seconds so they compare with persisted ones.
        self._cooldown_until: dict[str, float] = {}
        self._consecutive_429: dict[str, int] = {}
        self._next_slot: dict[str, float] = {}
        self._inflight: dict[tuple, asyncio.Future] = {}
        # aiohttp's per-request `proxy=` kwarg only understands HTTP(S) proxies;
        # SOCKS4/5 (e.g. a local Tor instance on Kali) requires a dedicated
        # connector instead, provided by the aiohttp-socks package.
        self._is_socks_proxy = bool(self.proxy) and self.proxy.startswith(
            ("socks4://", "socks4a://", "socks5://", "socks5h://")
        )

    async def __aenter__(self) -> "AsyncHttpClient":
        connector = None
        if self._is_socks_proxy:
            try:
                from aiohttp_socks import ProxyConnector
            except ImportError as exc:
                raise RuntimeError(
                    "SOCKS proxy configured but aiohttp-socks is not installed "
                    "(pip install aiohttp-socks)"
                ) from exc
            # "socks5h://"/"socks4a://" (the curl/Tor-docs convention meaning
            # "let the proxy resolve hostnames") aren't schemes python-socks
            # recognizes -- it only knows plain "socks5"/"socks4" and always
            # resolves remotely through the proxy for socks5 regardless, so
            # the "h"/"a" suffix is just stripped rather than needing any
            # different handling.
            proxy_url = self.proxy
            if proxy_url.startswith("socks5h://"):
                proxy_url = "socks5://" + proxy_url[len("socks5h://"):]
            elif proxy_url.startswith("socks4a://"):
                proxy_url = "socks4://" + proxy_url[len("socks4a://"):]
            connector = ProxyConnector.from_url(proxy_url)
        # A handful of real sites (e.g. Trakt.tv, whose Link: preload header
        # alone has run past 32KB in practice) send a single response header
        # past aiohttp's default 8190-byte line/field limit, which otherwise
        # fails the whole request with a LineTooLong/FieldTooLong parser
        # error before we ever see a status code. Give real-world pages
        # generous headroom -- this only raises how much we're willing to
        # buffer/parse, not a security-sensitive limit for a client.
        self._session = aiohttp.ClientSession(
            timeout=self.timeout, connector=connector,
            max_line_size=8190 * 16, max_field_size=8190 * 16,
        )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self.log_source_metrics()
        if self._session:
            await self._session.close()

    # -- per-source policy, metrics and state ---------------------------

    def set_source_policy(self, source: str, policy: SourcePolicy) -> None:
        """Give `source` its own request handling. Call before its first
        request -- an existing concurrency semaphore is replaced."""
        self._policies[source] = policy
        self._semaphores.pop(source, None)

    def _policy(self, source: str) -> SourcePolicy:
        return self._policies.get(source, _DEFAULT_POLICY)

    def _persisted(self, source: str) -> bool:
        return self._policy(source).persist_state and self.cache is not None and self.cache.enabled

    def source_metrics(self, source: str) -> SourceMetrics:
        if source not in self._metrics:
            self._metrics[source] = SourceMetrics()
        return self._metrics[source]

    def metrics_snapshot(self, sources: Optional[list[str]] = None) -> dict[str, dict[str, Any]]:
        """Counters per source -- by default for every source with its own
        policy (the ones worth watching individually)."""
        names = sources if sources is not None else list(self._policies)
        return {name: self.source_metrics(name).snapshot() for name in names}

    def log_source_metrics(self) -> None:
        # Counters only -- never headers, cookies, URLs or anything else a
        # request carried.
        for source, snap in self.metrics_snapshot().items():
            log.info(
                "source metrics %s: requests=%d ok=%d 429=%d network_errors=%d cache_hits=%d "
                "duplicates_prevented=%d cooldown_skips=%d peak_queued=%d avg_response_ms=%s cooldown_until=%s",
                source, snap["requests_sent"], snap["successful"], snap["rate_limited"], snap["network_errors"],
                snap["cache_hits"], snap["duplicates_prevented"], snap["cooldown_skips"], snap["peak_queued"],
                snap["avg_response_ms"], snap["cooldown_until"] or "-",
            )

    def _load_state(self, source: str) -> None:
        """Merge the persisted state for `source` into memory (never moving
        a cooldown earlier than one already known here)."""
        if not self._persisted(source):
            return
        try:
            state = self.cache.get_source_state(source)  # type: ignore[union-attr]
        except sqlite3.Error as exc:
            log.warning("could not read saved state for %s, using this run's only: %s", source, exc)
            return
        self._cooldown_until[source] = max(self._cooldown_until.get(source, 0.0), state["cooldown_until"])
        self._consecutive_429[source] = int(state["consecutive_429"])

    def _save_state(self, source: str, **fields: Any) -> None:
        if not self._persisted(source):
            return
        try:
            self.cache.update_source_state(source, **fields)  # type: ignore[union-attr]
        except sqlite3.Error as exc:
            log.warning("could not save state for %s: %s", source, exc)

    def cooldown_until(self, source: str) -> Optional[float]:
        """When `source` may be asked again (epoch seconds), or None if it
        isn't cooling down."""
        self._load_state(source)
        until = self._cooldown_until.get(source, 0.0)
        return until if until > time.time() else None

    def start_cooldown(self, source: str, retry_after: Optional[float] = None) -> float:
        """Stop sending requests to `source` for a while. With retry_after
        (the server's own answer) for exactly that long; otherwise for a
        bounded exponential backoff -- the base cooldown doubled for each
        consecutive 429, capped, with jitter that only ever lengthens it.
        An active cooldown is never shortened. Returns when it ends."""
        policy = self._policy(source)
        self._load_state(source)
        consecutive = self._consecutive_429.get(source, 0) + 1
        if retry_after is not None:
            duration = retry_after
            how = "as Retry-After asked"
        else:
            base = min(policy.max_cooldown, policy.cooldown * 2 ** min(consecutive - 1, 20))
            duration = min(policy.max_cooldown, base * random.uniform(1.0, 1.25))
            how = f"backoff after 429 #{consecutive}"
        until = max(time.time() + duration, self._cooldown_until.get(source, 0.0))
        self._cooldown_until[source] = until
        self._consecutive_429[source] = consecutive
        self._save_state(source, cooldown_until=until, consecutive_429=consecutive)
        self.source_metrics(source).cooldown_until = until
        log.info("%s rate-limited: no requests until %s (%s)", source, _iso(until), how)
        return until

    def _clear_backoff(self, source: str) -> None:
        # Any answer other than a 429 ends a run of consecutive 429s.
        if self._consecutive_429.get(source):
            self._consecutive_429[source] = 0
            self._save_state(source, consecutive_429=0)

    def _skip_if_cooling_down(self, source: str, url: str,
                              expected_statuses: Optional[set[int]]) -> Optional[HttpResponse]:
        until = self.cooldown_until(source)
        if until is None:
            return None
        metrics = self.source_metrics(source)
        metrics.cooldown_skips += 1
        metrics.cooldown_until = until
        log.debug("%s is cooling down until %s, not sending %s", source, _iso(until), url)
        return HttpResponse(
            status=429, text="", headers={}, url=url,
            ok=bool(expected_statuses and 429 in expected_statuses),
            fetched_at=time.time(), cooldown_until=until, skipped=True,
        )

    async def _wait_for_slot(self, source: str, policy: SourcePolicy) -> None:
        """Enforce the policy's minimum spacing between request starts."""
        if policy.min_interval <= 0:
            return
        now = time.time()
        slot = None
        if self._persisted(source):
            try:
                slot = self.cache.reserve_slot(source, policy.min_interval)  # type: ignore[union-attr]
            except sqlite3.Error as exc:
                log.warning("could not use the shared request schedule for %s: %s", source, exc)
        if slot is None:
            slot = max(now, self._next_slot.get(source, 0.0))
            self._next_slot[source] = slot + policy.min_interval
        delay = slot - now
        if delay > 0:
            log.debug("%s: waiting %.1fs to keep requests %.1fs apart", source, delay, policy.min_interval)
            await asyncio.sleep(delay)

    def _semaphore_for(self, source: str) -> asyncio.Semaphore:
        if source not in self._semaphores:
            limit = self._policy(source).max_concurrency or self.rate_limit_per_source
            self._semaphores[source] = asyncio.Semaphore(max(1, limit))
        return self._semaphores[source]

    def _headers(self, extra: Optional[dict[str, str]] = None) -> dict[str, str]:
        headers = {"User-Agent": self.user_agent, **self.default_headers}
        if extra:
            headers.update(extra)
        return headers

    async def _save_evidence(self, source: str, url: str, resp: HttpResponse) -> Optional[str]:
        if not self.save_evidence or not self.evidence_dir:
            return None
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        # Evidence is raw captured response bodies -- breach data, profile
        # PII, whatever a source returned -- so the directory and each file
        # shouldn't be readable by other accounts on a shared machine (this
        # tool explicitly targets Kali-style multi-tool boxes). No-op-ish on
        # Windows (only the read-only bit applies there).
        try:
            os.chmod(self.evidence_dir, 0o700)
        except OSError:
            pass
        fname = f"{source}_{uuid.uuid4().hex[:10]}.json"
        path = self.evidence_dir / fname
        payload = {
            "source": source,
            "url": url,
            "status": resp.status,
            "headers": resp.headers,
            "body": resp.text,
            "captured_at": time.time(),
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return str(path)

    async def request(
        self,
        source: str,
        method: str,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
        allow_cache: bool = True,
        expected_statuses: Optional[set[int]] = None,
    ) -> HttpResponse:
        """Perform a single request with caching, retries, and per-source rate limiting."""
        assert self._session is not None, "AsyncHttpClient must be used as an async context manager"

        cache_extra = json.dumps(params or {}, sort_keys=True)
        if allow_cache and self.cache is not None and method.upper() == "GET":
            cached = self.cache.get(source, method, url, cache_extra)
            if cached is not None:
                if self.stats:
                    self.stats.cache_hits += 1
                self.source_metrics(source).cache_hits += 1
                return HttpResponse(
                    status=cached["status_code"],
                    text=cached["body"] or "",
                    headers=cached["headers"],
                    # Where the request really ended up after redirects, so
                    # a cache hit can still tell e.g. a login-wall redirect
                    # apart from the profile page itself.
                    url=cached["headers"].get(_FINAL_URL_HEADER, url),
                    ok=200 <= cached["status_code"] < 400,
                    cached=True,
                    content=(cached["body"] or "").encode("utf-8", errors="replace"),
                    fetched_at=cached.get("fetched_at"),
                )

        send = self._send(source, method, url, headers=headers, params=params, json_body=json_body,
                          allow_cache=allow_cache, expected_statuses=expected_statuses, cache_extra=cache_extra)

        # Single-flight: an identical idempotent request already on its way
        # (same method, URL, query and extra headers) is joined instead of
        # sent again -- e.g. two identifiers that normalize to the same
        # profile URL, checked concurrently. Not for anything with a body.
        if method.upper() not in ("GET", "HEAD") or json_body is not None:
            return await send
        key = (method.upper(), url, cache_extra, json.dumps(headers or {}, sort_keys=True), allow_cache)
        inflight = self._inflight.get(key)
        if inflight is not None:
            send.close()
            self.source_metrics(source).duplicates_prevented += 1
            log.debug("%s: joining an identical in-flight request for %s", source, url)
            return await asyncio.shield(inflight)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            response = await send
        except asyncio.CancelledError:
            future.cancel()
            raise
        except BaseException as exc:
            future.set_exception(exc)
            future.exception()  # retrieved here; joiners still get it when they await
            raise
        else:
            future.set_result(response)
            return response
        finally:
            self._inflight.pop(key, None)

    async def _send(
        self,
        source: str,
        method: str,
        url: str,
        *,
        headers: Optional[dict[str, str]],
        params: Optional[dict[str, Any]],
        json_body: Optional[dict[str, Any]],
        allow_cache: bool,
        expected_statuses: Optional[set[int]],
        cache_extra: str,
    ) -> HttpResponse:
        policy = self._policy(source)
        metrics = self.source_metrics(source)
        max_retries = self.retries if policy.max_retries is None else max(0, policy.max_retries)
        # Only pass a timeout when the policy sets one: aiohttp reads an
        # explicit timeout=None as "no timeout at all", not "session default".
        request_kwargs: dict[str, Any] = {}
        if policy.request_timeout:
            request_kwargs["timeout"] = aiohttp.ClientTimeout(total=policy.request_timeout)
        sem = self._semaphore_for(source)
        last_error: Optional[str] = None

        metrics.queued += 1
        metrics.peak_queued = max(metrics.peak_queued, metrics.queued)
        queued = True
        try:
            async with sem:
                for attempt in range(max_retries + 1):
                    # Checked inside the semaphore, on every attempt and again
                    # after any spacing wait, so requests that were already
                    # queued when a 429 arrived also stand down instead of
                    # firing anyway.
                    skipped = self._skip_if_cooling_down(source, url, expected_statuses)
                    if skipped is not None:
                        return skipped
                    await self._wait_for_slot(source, policy)
                    skipped = self._skip_if_cooling_down(source, url, expected_statuses)
                    if skipped is not None:
                        return skipped
                    if queued:
                        metrics.queued -= 1
                        queued = False

                    if self.stats:
                        self.stats.requests_sent += 1
                    metrics.requests_sent += 1
                    started = time.monotonic()
                    try:
                        async with self._session.request(  # type: ignore[union-attr]
                            method,
                            url,
                            headers=self._headers(headers),
                            params=params,
                            json=json_body,
                            proxy=None if self._is_socks_proxy else self.proxy,
                            allow_redirects=True,
                            **request_kwargs,
                        ) as resp:
                            cooldown_until = None
                            if resp.status == 429:
                                metrics.rate_limited += 1
                                # Only retry when the server says how long to
                                # wait and it's short. Retrying a bare 429 a
                                # second later just spends more of a limit
                                # that's already exhausted (and on some sites
                                # lengthens the block), so otherwise stop, and
                                # put the whole source on cooldown so the rest
                                # of this run -- other identifiers, the decoy
                                # check -- and, for a persisted policy, the next
                                # runs too, don't keep hitting it either.
                                retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                                if (retry_after is not None and retry_after <= policy.max_retry_wait
                                        and attempt < max_retries):
                                    metrics.responses += 1
                                    metrics.total_response_seconds += time.monotonic() - started
                                    log.debug("429 from %s, retrying in %.1fs as asked (attempt %d/%d)",
                                              source, retry_after, attempt + 1, max_retries + 1)
                                    await asyncio.sleep(retry_after)
                                    continue
                                cooldown_until = self.start_cooldown(source, retry_after)
                            else:
                                self._clear_backoff(source)

                            # .read() first, then .text() -- aiohttp buffers the
                            # body on first read, so this is one network read,
                            # not two, and gives us both the raw bytes (needed
                            # for binary content like images) and the decoded
                            # text every other caller already relies on.
                            raw = await resp.read()
                            text = await resp.text(errors="replace")
                            metrics.responses += 1
                            metrics.total_response_seconds += time.monotonic() - started
                            if resp.status < 400:
                                metrics.successful += 1
                            ok = resp.status < 400 or (expected_statuses and resp.status in expected_statuses)
                            response = HttpResponse(
                                status=resp.status,
                                text=text,
                                headers=dict(resp.headers),
                                url=str(resp.url),
                                ok=bool(ok),
                                content=raw,
                                fetched_at=time.time(),
                                cooldown_until=cooldown_until,
                            )
                            if self.stats:
                                self.stats.requests_succeeded += 1

                            # A 429 says "not right now", not anything about the
                            # target -- caching it would replay the rate limit
                            # for the whole TTL (24h by default) on every rerun
                            # of the same identifier, long after the site has
                            # stopped limiting us. Same reasoning as 5xx.
                            if (allow_cache and self.cache is not None and method.upper() == "GET"
                                    and resp.status < 500 and resp.status != 429):
                                self.cache.put(source, method, url, resp.status, text,
                                               {**dict(resp.headers), _FINAL_URL_HEADER: str(resp.url)}, cache_extra)

                            response.evidence_path = await self._save_evidence(source, url, response)
                            return response
                    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                        # OSError covers failures that don't come from aiohttp
                        # itself -- notably aiohttp_socks/python_socks proxy
                        # errors (e.g. the configured SOCKS proxy isn't actually
                        # running), which otherwise propagate uncaught and crash
                        # the calling plugin with a raw traceback instead of a
                        # clean, per-source error.
                        metrics.network_errors += 1
                        last_error = str(exc) or type(exc).__name__
                        log.debug("request failed (%s attempt %d/%d): %s", source, attempt + 1, max_retries + 1, exc)
                        if attempt < max_retries:
                            await asyncio.sleep(self.retry_backoff ** attempt)
        finally:
            if queued:
                metrics.queued -= 1

        if self.stats:
            self.stats.requests_failed += 1
        return HttpResponse(status=0, text="", headers={}, url=url, ok=False, error=last_error,
                            fetched_at=time.time())

    async def get(self, source: str, url: str, **kwargs: Any) -> HttpResponse:
        return await self.request(source, "GET", url, **kwargs)

    async def head(self, source: str, url: str, **kwargs: Any) -> HttpResponse:
        return await self.request(source, "HEAD", url, allow_cache=False, **kwargs)
