import asyncio

import pytest
from aiohttp import web

from osintrecon.core.cache import ResponseCache
from osintrecon.core.http_client import AsyncHttpClient


async def _serve(status: int):
    """Start a throwaway local server that always answers with `status`."""
    hits = {"count": 0}

    async def handler(request):
        hits["count"] += 1
        return web.Response(status=status, text=f"status {status}")

    app = web.Application()
    app.router.add_get("/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}/user", hits


async def _fetch_twice(status: int, tmp_path):
    runner, url, hits = await _serve(status)
    cache = ResponseCache(str(tmp_path / "cache.sqlite3"))
    try:
        # Two separate clients = two separate runs sharing one cache.
        async with AsyncHttpClient(retries=0, cache=cache) as http:
            first = await http.get("test", url, expected_statuses={404, 429})
        async with AsyncHttpClient(retries=0, cache=cache) as http:
            second = await http.get("test", url, expected_statuses={404, 429})
    finally:
        cache.close()
        await runner.cleanup()
    return first, second, hits["count"]


def test_rate_limited_response_is_not_cached(tmp_path):
    first, second, requests_made = asyncio.run(_fetch_twice(429, tmp_path))
    assert first.status == 429 and second.status == 429
    assert second.cached is False
    assert requests_made == 2  # the rerun really asked the site again


def test_definitive_response_is_still_cached(tmp_path):
    first, second, requests_made = asyncio.run(_fetch_twice(404, tmp_path))
    assert second.status == 404
    assert second.cached is True
    assert requests_made == 1


async def _serve_routes(routes):
    """Local server with per-path handlers; returns (runner, base_url, hits)."""
    hits: dict[str, int] = {}
    app = web.Application()
    for path, handler in routes.items():
        async def counted(request, _handler=handler, _path=path):
            hits[_path] = hits.get(_path, 0) + 1
            return await _handler(request)
        app.router.add_get(path, counted)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}", hits


async def _limited(request):
    return web.Response(status=429)


async def _ok(request):
    return web.Response(status=200, text="ok")


def test_bare_429_is_not_retried_and_cools_the_source_down():
    async def scenario():
        runner, base, hits = await _serve_routes({"/a": _limited, "/b": _limited, "/other": _ok})
        try:
            async with AsyncHttpClient(retries=2) as http:
                first = await http.get("site", f"{base}/a", expected_statuses={429})
                second = await http.get("site", f"{base}/b", expected_statuses={429})
                other = await http.get("another-site", f"{base}/other")
        finally:
            await runner.cleanup()
        return first, second, other, hits

    first, second, other, hits = asyncio.run(scenario())
    assert first.status == 429 and second.status == 429
    assert hits["/a"] == 1          # no immediate retries against an exhausted limit
    assert "/b" not in hits         # same source, same run: never sent
    assert other.status == 200      # other sources are unaffected


def test_short_retry_after_is_honoured():
    calls = {"n": 0}

    async def limited_once(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return web.Response(status=429, headers={"Retry-After": "0"})
        return web.Response(status=200, text="ok")

    async def scenario():
        runner, base, hits = await _serve_routes({"/x": limited_once})
        try:
            async with AsyncHttpClient(retries=2) as http:
                return await http.get("site", f"{base}/x")
        finally:
            await runner.cleanup()

    resp = asyncio.run(scenario())
    assert resp.status == 200
    assert calls["n"] == 2


def test_final_url_after_redirect_survives_a_cache_hit(tmp_path):
    async def to_login(request):
        raise web.HTTPFound("/accounts/login/")

    async def scenario():
        runner, base, hits = await _serve_routes({"/someone/": to_login, "/accounts/login/": _ok})
        cache = ResponseCache(str(tmp_path / "cache.sqlite3"))
        try:
            async with AsyncHttpClient(retries=0, cache=cache) as http:
                fresh = await http.get("site", f"{base}/someone/")
                cached = await http.get("site", f"{base}/someone/")
        finally:
            cache.close()
            await runner.cleanup()
        return fresh, cached

    fresh, cached = asyncio.run(scenario())
    assert cached.cached is True
    assert fresh.url.endswith("/accounts/login/")
    assert cached.url == fresh.url


# -- per-source policies ---------------------------------------------------

def test_retry_after_parser_accepts_seconds_and_http_dates():
    import email.utils
    import time

    from osintrecon.core.http_client import _parse_retry_after

    now = time.time()
    assert _parse_retry_after("120") == 120
    assert _parse_retry_after(email.utils.formatdate(now + 300, usegmt=True), now=now) == pytest.approx(300, abs=1)
    assert _parse_retry_after(email.utils.formatdate(now - 300, usegmt=True), now=now) == 0
    for unusable in (None, "", "soon", "-5", "inf", "nan"):
        assert _parse_retry_after(unusable) is None


def test_minimum_interval_spaces_requests_even_across_separate_runs(tmp_path):
    from osintrecon.core.http_client import SourcePolicy

    started: list[float] = []

    async def stamp(request):
        import time
        started.append(time.monotonic())
        return web.Response(text="ok")

    async def scenario():
        runner, base, _ = await _serve_routes({"/x": stamp})
        cache = ResponseCache(str(tmp_path / "cache.sqlite3"))
        try:
            for _ in range(2):  # two runs, as two web searches would be
                async with AsyncHttpClient(cache=cache) as http:
                    http.set_source_policy("site", SourcePolicy(min_interval=0.4))
                    await http.get("site", f"{base}/x", allow_cache=False)
        finally:
            cache.close()
            await runner.cleanup()

    asyncio.run(scenario())
    assert len(started) == 2
    assert started[1] - started[0] >= 0.35


def test_identical_concurrent_requests_share_one_network_request():
    async def slow(request):
        await asyncio.sleep(0.2)
        return web.Response(text="ok")

    async def scenario():
        runner, base, hits = await _serve_routes({"/x": slow})
        try:
            async with AsyncHttpClient() as http:
                responses = await asyncio.gather(*(http.get("site", f"{base}/x") for _ in range(3)))
                return responses, hits, http.source_metrics("site").duplicates_prevented
        finally:
            await runner.cleanup()

    responses, hits, prevented = asyncio.run(scenario())
    assert [r.status for r in responses] == [200, 200, 200]
    assert hits["/x"] == 1
    assert prevented == 2


def test_per_source_timeout_applies_only_to_that_source():
    from osintrecon.core.http_client import SourcePolicy

    async def slow(request):
        await asyncio.sleep(0.5)
        return web.Response(text="ok")

    async def scenario():
        runner, base, _ = await _serve_routes({"/x": slow})
        try:
            async with AsyncHttpClient(timeout_seconds=5, retries=0) as http:
                http.set_source_policy("impatient", SourcePolicy(request_timeout=0.1, persist_state=False))
                impatient = await http.get("impatient", f"{base}/x")
                patient = await http.get("patient", f"{base}/x")
                return impatient, patient, http.source_metrics("impatient").network_errors
        finally:
            await runner.cleanup()

    impatient, patient, errors = asyncio.run(scenario())
    assert impatient.error is not None and errors == 1
    assert patient.status == 200


def test_metrics_log_line_carries_counters_only(caplog):
    import logging

    from osintrecon.core.http_client import SourcePolicy

    async def scenario():
        runner, base, _ = await _serve_routes({"/secret-path": _ok})
        try:
            async with AsyncHttpClient(default_headers={"Cookie": "sessionid=hunter2"}) as http:
                http.set_source_policy("site", SourcePolicy(persist_state=False))
                await http.get("site", f"{base}/secret-path?token=abc")
        finally:
            await runner.cleanup()

    logger = logging.getLogger("n1xYosint")
    previous = logger.propagate
    logger.propagate = True
    try:
        with caplog.at_level(logging.INFO, logger="n1xYosint.http"):
            asyncio.run(scenario())
    finally:
        logger.propagate = previous
    lines = [r.getMessage() for r in caplog.records if "source metrics" in r.getMessage()]
    assert len(lines) == 1 and "requests=1" in lines[0]
    assert "hunter2" not in lines[0] and "token" not in lines[0] and "secret-path" not in lines[0]


async def _stream_forever(request):
    # chunked, no Content-Length: only the running count can stop it
    resp = web.StreamResponse(status=200)
    await resp.prepare(request)
    chunk = b"x" * 65536
    for _ in range(64):  # 4 MiB, well past the 1 MiB cap below
        await resp.write(chunk)
    return resp


async def _declared_big(request):
    return web.Response(status=200, body=b"y" * (2 * 1024 * 1024))


def test_bodies_over_the_cap_are_refused_not_truncated():
    async def scenario():
        runner, base, hits = await _serve_routes({"/stream": _stream_forever, "/big": _declared_big, "/ok": _ok})
        try:
            async with AsyncHttpClient(retries=2, max_body_bytes=1024 * 1024) as http:
                streamed = await http.get("test", base + "/stream", allow_cache=False)
                declared = await http.get("test", base + "/big", allow_cache=False)
                small = await http.get("test", base + "/ok", allow_cache=False)
                lowered = await http.get("test", base + "/ok", allow_cache=False, max_bytes=1)
                # a caller can lower the cap, never raise it past the client's
                raised = await http.get("test", base + "/big", allow_cache=False, max_bytes=10 * 1024 * 1024)
        finally:
            await runner.cleanup()
        return streamed, declared, small, lowered, raised, hits

    streamed, declared, small, lowered, raised, hits = asyncio.run(scenario())
    for resp in (streamed, declared, raised):
        assert not resp.ok and resp.text == "" and resp.content == b""
        assert "larger than" in resp.error
    assert hits["/stream"] == 1  # not retried: the same server sends the same body
    assert small.ok and small.text == "ok"
    assert not lowered.ok and "larger than 1 bytes" in lowered.error


def test_text_is_decoded_with_the_declared_charset():
    async def latin1(request):
        return web.Response(body="café".encode("latin-1"), content_type="text/plain", charset="latin-1")

    async def scenario():
        runner, base, _ = await _serve_routes({"/l": latin1})
        try:
            async with AsyncHttpClient(retries=0) as http:
                return await http.get("test", base + "/l", allow_cache=False)
        finally:
            await runner.cleanup()

    assert asyncio.run(scenario()).text == "café"
