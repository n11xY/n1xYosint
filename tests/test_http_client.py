import asyncio

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
