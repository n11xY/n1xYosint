"""Instagram handling in username_sites: its own request policy, strict
outcome classification, result cache, cooldown and request deduplication.

Everything here runs against a local aiohttp server (or a fake client) --
no request ever goes to Instagram.
"""
import asyncio
import email.utils
import time
from collections import defaultdict

import pytest
from aiohttp import web

from osintrecon.core.cache import ResponseCache
from osintrecon.core.config import DEFAULT_CONFIG, Config
from osintrecon.core.http_client import AsyncHttpClient
from osintrecon.core.models import Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.sources import username_sites
from osintrecon.plugins.sources.username_sites import SiteSettings, UsernameSitesPlugin

IG = "username_sites:Instagram"
INSTAGRAM_DEFAULTS = DEFAULT_CONFIG["sources"]["username_sites"]["site_settings"]["Instagram"]


@pytest.fixture(autouse=True)
def no_jitter(monkeypatch):
    monkeypatch.setattr(username_sites, "_JITTER_SECONDS", (0, 0))


def profile_page(username):
    # Instagram writes "@" and the bullet as entities in its titles.
    return f"<title>Some Name (&#064;{username}) &#x2022; Instagram photos and videos</title>"


ERROR_SHELL = '<title>Instagram</title> ... "pageID":"httpErrorPage"'


def instagram_site(base, **extra):
    site = {
        "name": "Instagram", "category": "social", "url": base + "/ig/{}/", "check": "message",
        "found_text": ["Instagram photos and videos", "@{}"], "login_wall": "/accounts/login",
        "lowercase": True, "username_pattern": "^[a-z0-9._]{1,30}$",
    }
    site.update(extra)
    return site


def other_site(base):
    return {"name": "Other", "category": "social", "url": base + "/other/{}", "check": "status", "found_status": 200}


class Server:
    """Local stand-in for the sites: /ig/<user>/ runs `instagram`,
    /other/<user> runs `other`, and every hit is counted per path."""

    def __init__(self, instagram, other=None):
        self.instagram = instagram
        self.other = other or (lambda request: web.Response(status=200, text="ok"))
        self.hits = defaultdict(int)

    async def __aenter__(self):
        async def ig(request):
            self.hits[request.path] += 1
            result = self.instagram(request)
            return await result if asyncio.iscoroutine(result) else result

        async def login(request):
            self.hits[request.path] += 1
            return web.Response(status=200, text="<title>Login • Instagram</title>")

        async def other(request):
            self.hits[request.path] += 1
            return self.other(request)

        app = web.Application()
        app.router.add_get("/ig/{user}/", ig)
        app.router.add_get("/accounts/login/", login)
        app.router.add_get("/other/{user}", other)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        return self

    async def __aexit__(self, *exc):
        await self.runner.cleanup()

    def instagram_hits(self):
        return sum(n for path, n in self.hits.items() if path.startswith("/ig/"))


def settings(**overrides):
    # Defaults as shipped, minus the spacing so tests don't sleep.
    return {**INSTAGRAM_DEFAULTS, "minimum_request_interval": 0, **overrides}


async def scan(server, cache_path, usernames, *, site_overrides=None, with_other=False, **setting_overrides):
    """One run: a fresh client and plugin (as each search gets), sharing the
    cache database at cache_path with any earlier run."""
    cache = ResponseCache(str(cache_path))
    try:
        async with AsyncHttpClient(retries=2, cache=cache) as http:
            plugin = UsernameSitesPlugin({"site_settings": {"Instagram": settings(**setting_overrides)}}, http)
            plugin.sites = [instagram_site(server.base, **(site_overrides or {}))]
            if with_other:
                plugin.sites.append(other_site(server.base))
            results = await asyncio.gather(
                *(plugin.run(Identifier(value=name, type=IdentifierType.USERNAME)) for name in usernames)
            )
            metrics = http.source_metrics(IG).snapshot()
    finally:
        cache.close()
    return results, metrics


def only(findings, source=IG):
    matching = [f for f in findings if f.source == source]
    assert len(matching) == 1, matching
    return matching[0]


# -- configuration ---------------------------------------------------------

def test_instagram_has_its_own_conservative_provider_configuration():
    raw = Config().source_config("username_sites")["site_settings"]["Instagram"]
    parsed = SiteSettings.from_config(raw)
    assert parsed.enabled is True
    assert parsed.decoy_verification_enabled is False
    assert parsed.cache_ttl == 86400 and parsed.negative_cache_ttl == 3600
    policy = parsed.policy
    assert policy.max_concurrency == 1
    assert policy.request_timeout == 15
    assert policy.min_interval == 5
    assert policy.max_retries == 0
    assert policy.cooldown == 900 and policy.max_cooldown == 21600


def test_a_config_file_overrides_single_instagram_settings(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("sources:\n  username_sites:\n    site_settings:\n      Instagram:\n        cooldown_duration: 60\n")
    raw = Config.load(str(cfg_file)).source_config("username_sites")["site_settings"]["Instagram"]
    assert raw["cooldown_duration"] == 60
    assert raw["max_concurrency"] == 1  # untouched defaults survive the merge


def test_disabled_instagram_sends_nothing(tmp_path):
    async def go():
        async with Server(lambda r: web.Response(text=profile_page("someone"))) as server:
            results, _ = await scan(server, tmp_path / "c.db", ["someone"], enabled=False)
            return results, server.instagram_hits()

    results, hits = asyncio.run(go())
    assert results == [[]]
    assert hits == 0


# -- classification ----------------------------------------------------------

def test_profile_page_is_found_fresh_without_a_decoy_request(tmp_path):
    async def go():
        async with Server(lambda r: web.Response(text=profile_page(r.match_info["user"]))) as server:
            results, metrics = await scan(server, tmp_path / "c.db", ["Cristiano"])
            return results, dict(server.hits), metrics

    results, hits, metrics = asyncio.run(go())
    finding = only(results[0])
    assert finding.status == MatchStatus.PROBABLE
    assert finding.metadata["outcome"] == "profile_found"
    assert finding.metadata["freshness"] == "fresh"
    assert finding.metadata["collected_at"]
    assert finding.metadata["decoy_check"] == "disabled for this site"
    assert finding.source_url.endswith("/ig/cristiano/")  # normalized before the request
    assert hits == {"/ig/cristiano/": 1}
    assert metrics["requests_sent"] == 1 and metrics["successful"] == 1


def test_a_generic_page_that_is_not_about_this_username_is_not_a_profile(tmp_path):
    async def go():
        async with Server(lambda r: web.Response(text="<title>Instagram photos and videos</title>")) as server:
            results, _ = await scan(server, tmp_path / "c.db", ["someone"])
            return results

    finding = only(asyncio.run(go())[0])
    assert finding.status == MatchStatus.ERROR
    assert finding.metadata["outcome"] == "unknown"
    assert finding.metadata["freshness"] == "unavailable"


def test_error_shell_is_unknown_never_not_found(tmp_path):
    async def go():
        async with Server(lambda r: web.Response(text=ERROR_SHELL)) as server:
            results, _ = await scan(server, tmp_path / "c.db", ["someone"])
            return results

    finding = only(asyncio.run(go())[0])
    assert finding.status == MatchStatus.ERROR
    assert finding.metadata["outcome"] == "unknown"
    assert finding.metadata["blocked"] == "unreadable_page"


def test_404_alone_is_not_treated_as_not_found_for_instagram(tmp_path):
    # Instagram's entry declares no not-found signal, so a bare 404 is
    # "couldn't tell", not "doesn't exist".
    async def go():
        async with Server(lambda r: web.Response(status=404, text="nope")) as server:
            results, _ = await scan(server, tmp_path / "c.db", ["someone"])
            return results

    finding = only(asyncio.run(go())[0])
    assert finding.metadata["outcome"] == "unknown"


def test_404_is_not_found_where_the_site_declares_that_meaning_and_is_cached_briefly(tmp_path):
    async def go():
        async with Server(lambda r: web.Response(status=404, text="nope")) as server:
            first, _ = await scan(server, tmp_path / "c.db", ["someone"], site_overrides={"not_found_status": 404})
            second, metrics = await scan(server, tmp_path / "c.db", ["someone"],
                                         site_overrides={"not_found_status": 404})
            return first, second, server.instagram_hits(), metrics

    first, second, hits, metrics = asyncio.run(go())
    assert first == [[]] and second == [[]]
    assert hits == 1                      # the second run used the negative cache
    assert metrics["cache_hits"] == 1
    cache = ResponseCache(str(tmp_path / "c.db"))
    stored = cache.get_result(IG, "someone")
    cache.close()
    assert stored["payload"]["outcome"] == "not_found"
    assert stored["expires_at"] - stored["collected_at"] == pytest.approx(INSTAGRAM_DEFAULTS["negative_cache_ttl"])


def test_login_wall_is_access_restricted_and_pauses_instagram(tmp_path):
    def redirect(request):
        raise web.HTTPFound("/accounts/login/?next=%2Fsomeone%2F")

    async def go():
        async with Server(redirect) as server:
            first, _ = await scan(server, tmp_path / "c.db", ["someone"])
            second, metrics = await scan(server, tmp_path / "c.db", ["another"])
            return first, second, server.instagram_hits(), metrics

    first, second, hits, metrics = asyncio.run(go())
    restricted = only(first[0])
    assert restricted.metadata["outcome"] == "access_restricted"
    assert restricted.metadata["blocked"] == "login_wall"
    paused = only(second[0])
    assert paused.metadata["outcome"] == "rate_limited"
    assert paused.metadata["request_sent"] is False
    assert hits == 1
    assert metrics["cooldown_skips"] == 1


# -- HTTP 429 ------------------------------------------------------------------

def test_429_with_retry_after_seconds_is_not_retried_and_the_pause_persists(tmp_path):
    async def go():
        async with Server(lambda r: web.Response(status=429, headers={"Retry-After": "120"})) as server:
            # Even with retries allowed, a 120s Retry-After is longer than the
            # client will wait inside one lookup.
            first, m1 = await scan(server, tmp_path / "c.db", ["someone"], max_retry_attempts=2)
            second, m2 = await scan(server, tmp_path / "c.db", ["someone"])
            return first, second, server.instagram_hits(), m1, m2

    before = time.time()
    first, second, hits, m1, m2 = asyncio.run(go())
    limited = only(first[0])
    assert limited.status == MatchStatus.ERROR
    assert limited.metadata["outcome"] == "rate_limited"
    assert limited.metadata["http_status"] == 429
    assert limited.metadata["request_sent"] is True
    assert m1["rate_limited"] == 1 and m1["requests_sent"] == 1

    cache = ResponseCache(str(tmp_path / "c.db"))
    state = cache.get_source_state(IG)
    cache.close()
    assert before + 115 <= state["cooldown_until"] <= time.time() + 121

    paused = only(second[0])
    assert paused.metadata["request_sent"] is False
    assert "paused until" in paused.title
    assert hits == 1                      # the second run sent nothing
    assert m2["cooldown_skips"] == 1 and m2["requests_sent"] == 0


def test_429_with_an_http_date_retry_after_is_honoured(tmp_path):
    retry_at = email.utils.formatdate(time.time() + 300, usegmt=True)

    async def go():
        async with Server(lambda r: web.Response(status=429, headers={"Retry-After": retry_at})) as server:
            await scan(server, tmp_path / "c.db", ["someone"])

    asyncio.run(go())
    cache = ResponseCache(str(tmp_path / "c.db"))
    until = cache.get_source_state(IG)["cooldown_until"]
    cache.close()
    assert time.time() + 290 <= until <= time.time() + 301


def test_429_without_retry_after_backs_off_exponentially_with_bounded_jitter(tmp_path):
    db = tmp_path / "c.db"

    async def one_run():
        async with Server(lambda r: web.Response(status=429)) as server:
            await scan(server, db, ["someone"], cooldown_duration=100, max_cooldown_duration=1000)
        cache = ResponseCache(str(db))
        state = cache.get_source_state(IG)
        # Let the pause run out (keeping the 429 count) so the next run asks again.
        cache.update_source_state(IG, cooldown_until=0)
        cache.close()
        return state["cooldown_until"] - time.time(), state["consecutive_429"]

    observed = [asyncio.run(one_run()) for _ in range(5)]
    for number, ((duration, count), base) in enumerate(zip(observed, (100, 200, 400, 800, 1000)), start=1):
        assert count == number
        assert base - 2 <= duration <= min(1000, base * 1.25) + 1  # jitter only lengthens; cap holds


def test_a_successful_answer_resets_the_backoff(tmp_path):
    db = tmp_path / "c.db"
    answers = iter([web.Response(status=429), web.Response(text=profile_page("someone"))])

    async def go():
        async with Server(lambda r: next(answers)) as server:
            await scan(server, db, ["someone"], cooldown_duration=100)
            cache = ResponseCache(str(db))
            cache.update_source_state(IG, cooldown_until=0)
            cache.close()
            await scan(server, db, ["someone"])

    asyncio.run(go())
    cache = ResponseCache(str(db))
    assert cache.get_source_state(IG)["consecutive_429"] == 0
    cache.close()


# -- deduplication and caching ---------------------------------------------

def test_duplicate_concurrent_lookups_share_one_request(tmp_path):
    async def slow_profile(request):
        await asyncio.sleep(0.2)
        return web.Response(text=profile_page(request.match_info["user"]))

    async def go():
        async with Server(slow_profile) as server:
            results, metrics = await scan(server, tmp_path / "c.db", ["Cristiano", "cristiano", "CRISTIANO"])
            return results, server.instagram_hits(), metrics

    results, hits, metrics = asyncio.run(go())
    assert hits == 1
    assert metrics["duplicates_prevented"] == 2
    found = [only(findings) for findings in results]
    assert {f.identifier.value for f in found} == {"Cristiano", "cristiano", "CRISTIANO"}
    assert all(f.metadata["outcome"] == "profile_found" for f in found)
    assert len({f.metadata["collected_at"] for f in found}) == 1


def test_usernames_instagram_cannot_have_are_never_requested(tmp_path):
    async def go():
        async with Server(lambda r: web.Response(text=profile_page("x"))) as server:
            results, _ = await scan(server, tmp_path / "c.db", ["john-doe", "a" * 31])
            return results, server.instagram_hits()

    results, hits = asyncio.run(go())
    assert results == [[], []]
    assert hits == 0


def test_found_result_is_reused_from_cache_until_it_expires(tmp_path):
    db = tmp_path / "c.db"

    async def go():
        async with Server(lambda r: web.Response(text=profile_page(r.match_info["user"]))) as server:
            fresh, _ = await scan(server, db, ["someone"])
            cached, cached_metrics = await scan(server, db, ["someone"])
            # Age the stored result past its TTL.
            cache = ResponseCache(str(db))
            stored = cache.get_result(IG, "someone")
            cache.put_result(IG, "someone", stored["payload"], ttl_seconds=86400,
                             collected_at=time.time() - 90000)
            cache.close()
            refreshed, _ = await scan(server, db, ["someone"])
            return fresh, cached, refreshed, cached_metrics, server.instagram_hits()

    fresh, cached, refreshed, cached_metrics, hits = asyncio.run(go())
    fresh_f, cached_f, refreshed_f = only(fresh[0]), only(cached[0]), only(refreshed[0])
    assert fresh_f.metadata["freshness"] == "fresh"
    assert cached_f.metadata["freshness"] == "cached"
    assert cached_f.metadata["collected_at"] == fresh_f.metadata["collected_at"]  # original time kept
    assert cached_f.status == MatchStatus.PROBABLE
    assert cached_metrics["cache_hits"] == 1 and cached_metrics["requests_sent"] == 0
    assert refreshed_f.metadata["freshness"] == "fresh"
    assert hits == 2


def test_stale_found_result_is_shown_only_as_uncertain_while_instagram_is_unavailable(tmp_path):
    db = tmp_path / "c.db"
    cache = ResponseCache(str(db))
    payload = {"outcome": "profile_found", "status": "probable", "http_status": 200, "decoy_check": "disabled"}
    cache.put_result(IG, "recent", payload, ttl_seconds=86400, collected_at=time.time() - 2 * 86400)
    cache.put_result(IG, "ancient", payload, ttl_seconds=86400, collected_at=time.time() - 30 * 86400)
    cache.close()

    async def go():
        async with Server(lambda r: web.Response(status=429)) as server:
            results, _ = await scan(server, db, ["recent"])
            cache = ResponseCache(str(db))
            cache.reset_source_state(IG)  # let the next run ask again
            cache.close()
            older, _ = await scan(server, db, ["ancient"])
            return results, older

    results, older = asyncio.run(go())
    stale = only(results[0])
    assert stale.status == MatchStatus.UNCERTAIN
    assert stale.metadata["freshness"] == "stale"
    assert stale.metadata["unavailable_reason"] == "rate_limited"
    assert "stale" in stale.title
    too_old = only(older[0])  # past stale_ttl: just "unavailable"
    assert too_old.status == MatchStatus.ERROR
    assert too_old.metadata["outcome"] == "rate_limited"


def test_429_is_never_cached_as_a_result(tmp_path):
    async def go():
        async with Server(lambda r: web.Response(status=429)) as server:
            await scan(server, tmp_path / "c.db", ["someone"])

    asyncio.run(go())
    cache = ResponseCache(str(tmp_path / "c.db"))
    assert cache.get_result(IG, "someone") is None
    cache.close()


def test_results_can_be_invalidated(tmp_path):
    cache = ResponseCache(str(tmp_path / "c.db"))
    cache.put_result(IG, "a", {"outcome": "profile_found"}, ttl_seconds=60)
    cache.put_result(IG, "b", {"outcome": "profile_found"}, ttl_seconds=60)
    assert cache.invalidate_results(IG, "a") == 1
    assert cache.get_result(IG, "a") is None and cache.get_result(IG, "b") is not None
    assert cache.invalidate_results(IG) == 1
    assert cache.get_result(IG, "b") is None
    cache.close()


# -- decoy verification --------------------------------------------------------

def test_decoy_can_be_turned_back_on_and_an_unreadable_decoy_is_not_a_pass(tmp_path):
    def handler(request):
        user = request.match_info["user"]
        return web.Response(text=profile_page(user) if user == "someone" else ERROR_SHELL)

    async def go():
        async with Server(handler) as server:
            results, _ = await scan(server, tmp_path / "c.db", ["someone"], decoy_verification_enabled=True)
            return results, server.instagram_hits()

    results, hits = asyncio.run(go())
    finding = only(results[0])
    assert hits == 2  # the profile and the decoy
    assert finding.metadata["decoy_check"] == "inconclusive (decoy request came back unknown)"
    assert finding.status == MatchStatus.PROBABLE


def test_a_rate_limited_decoy_on_any_site_is_inconclusive_not_passed(tmp_path):
    def other(request):
        return web.Response(status=200 if request.match_info["user"] == "someone" else 429)

    async def go():
        async with Server(lambda r: web.Response(text=ERROR_SHELL), other=other) as server:
            results, _ = await scan(server, tmp_path / "c.db", ["someone"], with_other=True)
            return results

    finding = only(asyncio.run(go())[0], source="username_sites:Other")
    assert finding.status == MatchStatus.CONFIRMED
    assert finding.metadata["decoy_check"] == "inconclusive (decoy request came back rate_limited)"


# -- isolation ---------------------------------------------------------------

def test_instagram_unavailable_while_other_sites_succeed(tmp_path):
    def other(request):
        return web.Response(status=200 if request.match_info["user"] == "someone" else 404)

    async def go():
        async with Server(lambda r: web.Response(status=429), other=other) as server:
            results, _ = await scan(server, tmp_path / "c.db", ["someone"], with_other=True)
            return results

    findings = asyncio.run(go())[0]
    assert only(findings).metadata["outcome"] == "rate_limited"
    other_finding = only(findings, source="username_sites:Other")
    assert other_finding.status == MatchStatus.CONFIRMED
    assert other_finding.metadata["decoy_check"] == "passed"


class _Resp:
    def __init__(self, status):
        self.status, self.text, self.error, self.cached, self.evidence_path = status, "", None, False, None


class _AnswersEverything:
    async def get(self, source, url, headers=None, expected_statuses=None):
        return _Resp(404 if "nx1" in url else 200)  # the decoy is never found


def test_one_site_failing_unexpectedly_costs_only_that_site():
    plugin = UsernameSitesPlugin(config={}, http=_AnswersEverything())
    plugin.sites = [
        {"name": "Broken", "url": "https://broken.example/{}", "check": "status", "username_pattern": "("},
        {"name": "Fine", "url": "https://fine.example/{}", "check": "status", "found_status": 200},
    ]

    findings = asyncio.run(plugin.run(Identifier(value="someone", type=IdentifierType.USERNAME)))

    broken = only(findings, source="username_sites:Broken")
    assert broken.status == MatchStatus.ERROR
    assert "internal error" in broken.title
    fine = only(findings, source="username_sites:Fine")
    assert fine.status == MatchStatus.CONFIRMED


# -- through the engine --------------------------------------------------------

def test_engine_applies_the_instagram_defaults_and_reports_its_metrics(tmp_path):
    import json

    from osintrecon.core.engine import Engine
    from osintrecon.plugins.registry import discover_builtin

    async def go():
        async with Server(lambda r: web.Response(status=429)) as server:
            sites_db = tmp_path / "sites.json"
            sites_db.write_text(json.dumps([instagram_site(server.base)]), encoding="utf-8")
            config = Config()
            config.data["cache"]["path"] = str(tmp_path / "c.db")
            sources = config.data["sources"]
            for cls in discover_builtin():  # only username_sites runs -- nothing leaves this machine
                sources.setdefault(cls.name, {})["enabled"] = cls.name == "username_sites"
            sources["username_sites"]["sites_db"] = str(sites_db)
            sources["username_sites"]["site_settings"]["Instagram"]["minimum_request_interval"] = 0
            result = await Engine(config).run(
                [Identifier(value=name, type=IdentifierType.USERNAME) for name in ("first", "second")]
            )
            return result, server.instagram_hits()

    result, hits = asyncio.run(go())
    assert hits == 1  # the first 429 paused Instagram for the rest of the run
    outcomes = sorted(f.metadata["request_sent"] for f in result.findings)
    assert outcomes == [False, True]
    metrics = result.source_metrics[IG]
    assert metrics["requests_sent"] == 1
    assert metrics["rate_limited"] == 1
    assert metrics["cooldown_skips"] == 1
    assert metrics["cooling_down"] is True
