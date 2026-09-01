"""Async HTTP client wrapper around aiohttp.

Provides: configurable timeouts, bounded retries with exponential backoff,
per-source concurrency limiting (semaphores), proxy support, custom headers /
user-agent, transparent response caching, and evidence (raw response) capture.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import aiohttp

from osintrecon.core.cache import ResponseCache
from osintrecon.core.logging_setup import get_logger
from osintrecon.core.models import RunStats

log = get_logger("http")


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
        if self._session:
            await self._session.close()

    def _semaphore_for(self, source: str) -> asyncio.Semaphore:
        if source not in self._semaphores:
            self._semaphores[source] = asyncio.Semaphore(self.rate_limit_per_source)
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
                return HttpResponse(
                    status=cached["status_code"],
                    text=cached["body"] or "",
                    headers=cached["headers"],
                    url=url,
                    ok=200 <= cached["status_code"] < 400,
                    cached=True,
                )

        sem = self._semaphore_for(source)
        last_error: Optional[str] = None

        async with sem:
            for attempt in range(self.retries + 1):
                if self.stats:
                    self.stats.requests_sent += 1
                try:
                    async with self._session.request(
                        method,
                        url,
                        headers=self._headers(headers),
                        params=params,
                        json=json_body,
                        proxy=None if self._is_socks_proxy else self.proxy,
                        allow_redirects=True,
                    ) as resp:
                        if resp.status == 429 and attempt < self.retries:
                            # Rate-limited: worth a delayed retry rather than
                            # giving up immediately. Respect Retry-After if
                            # the server sent one (seconds; HTTP-date values
                            # are rare for APIs and not worth parsing here),
                            # otherwise fall back to the normal backoff,
                            # capped so one slow site can't stall the run.
                            retry_after = resp.headers.get("Retry-After")
                            delay = self.retry_backoff ** attempt
                            if retry_after:
                                try:
                                    delay = max(delay, float(retry_after))
                                except ValueError:
                                    pass
                            delay = min(delay, 30)
                            log.debug("429 from %s, retrying in %.1fs (attempt %d/%d)",
                                      source, delay, attempt + 1, self.retries + 1)
                            await asyncio.sleep(delay)
                            continue

                        text = await resp.text(errors="replace")
                        ok = resp.status < 400 or (expected_statuses and resp.status in expected_statuses)
                        response = HttpResponse(
                            status=resp.status,
                            text=text,
                            headers=dict(resp.headers),
                            url=str(resp.url),
                            ok=bool(ok),
                        )
                        if self.stats:
                            self.stats.requests_succeeded += 1

                        if allow_cache and self.cache is not None and method.upper() == "GET" and resp.status < 500:
                            self.cache.put(source, method, url, resp.status, text, dict(resp.headers), cache_extra)

                        response.evidence_path = await self._save_evidence(source, url, response)
                        return response
                except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                    # OSError covers failures that don't come from aiohttp
                    # itself -- notably aiohttp_socks/python_socks proxy
                    # errors (e.g. the configured SOCKS proxy isn't actually
                    # running), which otherwise propagate uncaught and crash
                    # the calling plugin with a raw traceback instead of a
                    # clean, per-source error.
                    last_error = str(exc) or type(exc).__name__
                    log.debug("request failed (%s attempt %d/%d): %s", source, attempt + 1, self.retries + 1, exc)
                    if attempt < self.retries:
                        await asyncio.sleep(self.retry_backoff ** attempt)

        if self.stats:
            self.stats.requests_failed += 1
        return HttpResponse(status=0, text="", headers={}, url=url, ok=False, error=last_error)

    async def get(self, source: str, url: str, **kwargs: Any) -> HttpResponse:
        return await self.request(source, "GET", url, **kwargs)

    async def head(self, source: str, url: str, **kwargs: Any) -> HttpResponse:
        return await self.request(source, "HEAD", url, allow_cache=False, **kwargs)
