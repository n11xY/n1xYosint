"""Discord's username check: cached answers, name validation and a 429 pause
that holds across runs -- against a local server, never discord.com."""
import asyncio
import json

import pytest
from aiohttp import web

from osintrecon.core.cache import ResponseCache
from osintrecon.core.config import DEFAULT_CONFIG
from osintrecon.core.http_client import AsyncHttpClient
from osintrecon.core.models import Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.sources import discord

DEFAULTS = {**DEFAULT_CONFIG["sources"]["discord"], "minimum_request_interval": 0}


class Server:
    def __init__(self, handler):
        self.handler, self.bodies = handler, []

    async def __aenter__(self):
        async def endpoint(request):
            body = await request.json()
            self.bodies.append(body["username"])
            return self.handler(body["username"])

        app = web.Application()
        app.router.add_post("/check", endpoint)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/check"
        return self

    async def __aexit__(self, *exc):
        await self.runner.cleanup()


def taken(value):
    return web.json_response({"taken": True})


def free(value):
    return web.json_response({"taken": False})


def run_searches(tmp_path, monkeypatch, handler, names_per_run, **overrides):
    async def go():
        async with Server(handler) as server:
            monkeypatch.setattr(discord, "API_URL", server.url)
            out = []
            for names in names_per_run:
                cache = ResponseCache(str(tmp_path / "c.db"))
                try:
                    async with AsyncHttpClient(retries=2, cache=cache) as http:
                        plugin = discord.DiscordPlugin({**DEFAULTS, **overrides}, http)
                        out.append([await plugin.run(Identifier(value=n, type=IdentifierType.USERNAME))
                                    for n in names])
                finally:
                    cache.close()
            return out, server.bodies
    return asyncio.run(go())


def test_a_taken_name_is_confirmed_and_the_answer_is_reused(tmp_path, monkeypatch):
    runs, sent = run_searches(tmp_path, monkeypatch, taken, [["Wumpus"], ["wumpus"]])
    (first,), = runs[0]
    (second,), = runs[1]
    assert first.status == MatchStatus.CONFIRMED
    assert first.title == "Discord username taken: wumpus"   # lowercased, as Discord stores it
    assert first.metadata["data_origin"] == "direct"
    assert second.metadata["data_origin"] == "cache" and second.metadata["cached"] is True
    assert sent == ["wumpus"]                                 # the second search asked nothing


def test_an_available_name_is_no_finding_and_is_remembered(tmp_path, monkeypatch):
    runs, sent = run_searches(tmp_path, monkeypatch, free, [["nobody_here"], ["nobody_here"]])
    assert runs == [[[]], [[]]]
    assert sent == ["nobody_here"]


def test_names_discord_cannot_have_are_never_sent(tmp_path, monkeypatch):
    runs, sent = run_searches(tmp_path, monkeypatch, taken, [["a", "has space", "a..b", "x" * 33, "ok.name_1"]])
    assert [len(r) for r in runs[0]] == [0, 0, 0, 0, 1]
    assert sent == ["ok.name_1"]


def test_a_429_pauses_discord_across_runs_for_as_long_as_retry_after_says(tmp_path, monkeypatch):
    def limited(value):
        return web.Response(status=429, headers={"Retry-After": "1005"},
                            text=json.dumps({"message": "The resource is being rate limited.",
                                             "retry_after": 1004.2, "global": False}))

    runs, sent = run_searches(tmp_path, monkeypatch, limited, [["first_name"], ["second_name", "third_name"]])
    (limited_finding,), = runs[0]
    assert limited_finding.status == MatchStatus.ERROR
    assert limited_finding.metadata["outcome"] == "rate_limited"
    assert limited_finding.metadata["source_status"] == "RATE_LIMITED"
    assert limited_finding.metadata["cooldown_until"]
    assert "paused until" in limited_finding.title
    paused = [f for (f,) in runs[1]]
    assert all(f.metadata["request_sent"] is False and f.metadata["suppressed_by_cooldown"] for f in paused)
    assert all(f.metadata["http_status"] is None for f in paused)
    assert sent == ["first_name"]                             # nothing sent while paused, never retried


def test_reserved_names_and_odd_answers(tmp_path, monkeypatch):
    runs, _ = run_searches(tmp_path, monkeypatch, lambda v: web.json_response({"code": 50035}, status=400),
                           [["everyone"]])
    assert runs == [[[]]]
    other = tmp_path / "other"
    other.mkdir()
    runs, _ = run_searches(other, monkeypatch, lambda v: web.json_response({"unexpected": 1}), [["someone_new"]])
    (odd,), = runs[0]
    assert odd.status == MatchStatus.ERROR
    assert odd.metadata["source_status"] == "TEMPORARILY_UNAVAILABLE"


def test_a_network_failure_is_unavailable_never_available(monkeypatch, tmp_path):
    monkeypatch.setattr(discord, "API_URL", "http://127.0.0.1:9/check")

    async def go():
        async with AsyncHttpClient(retries=0, timeout_seconds=2) as http:
            plugin = discord.DiscordPlugin({**DEFAULTS, "request_timeout": 2}, http)
            return await plugin.run(Identifier(value="someone", type=IdentifierType.USERNAME))

    (finding,) = asyncio.run(go())
    assert finding.metadata["outcome"] == "network_error"
    assert finding.metadata["source_status"] == "TEMPORARILY_UNAVAILABLE"


@pytest.mark.parametrize("name", ["discord", "wumpus", "a_b.c", "x1"])
def test_valid_discord_names(name):
    assert discord.USERNAME_RE.fullmatch(name)
