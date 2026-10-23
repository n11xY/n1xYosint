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
        async with AsyncHttpClient(retries=0, cache=cache) as http:
            first = await http.get("test", url, expected_statuses={404, 429})
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
