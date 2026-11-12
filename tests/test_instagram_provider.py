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
    assert parsed.cache_ttl == 604800 and parsed.negative_cache_ttl == 3600
    policy = parsed.policy
    assert policy.max_concurrency == 1
    assert policy.request_timeout == 15
    assert policy.min_interval == 2
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


# -- restrictions observed live (2026-11-06) and uniform source status --------

def production_restrictions():
    """The restriction markers instagram.com itself answered with (2026-11-06),
    for a direct check like the one sites.json used to ship."""
    return {
        "httpErrorPage": "error_page",
        "\"require_login\":true": "auth_required",
        "challenge_required": "challenge_required",
        "checkpoint_required": "challenge_required",
        "feedback_required": "rate_limited",
        "Please wait a few minutes": "rate_limited",
    }


def restricted_scan(tmp_path, handler, names=("someone",), runs=1, **setting_overrides):
    async def go():
        async with Server(handler) as server:
            out = []
            for _ in range(runs):
                results, metrics = await scan(server, tmp_path / "c.db", list(names),
                                              site_overrides={"restrictions": production_restrictions()},
                                              **setting_overrides)
                out.append((results, metrics))
            return out, server.instagram_hits()
    return asyncio.run(go())


def test_the_error_shell_is_a_restriction_that_pauses_instagram(tmp_path):
    # What a home connection got for a real account and a random name alike.
    runs, hits = restricted_scan(tmp_path, lambda r: web.Response(text=ERROR_SHELL), runs=2)
    first = only(runs[0][0][0])
    assert first.status == MatchStatus.ERROR
    assert first.metadata["outcome"] == "access_restricted"
    assert first.metadata["blocked"] == "error_page"
    assert first.metadata["source_status"] == "TEMPORARILY_UNAVAILABLE"
    assert first.metadata["retry_eligible"] is True and first.metadata["cooldown_until"]
    assert "error page" in first.title
    second = only(runs[1][0][0])
    assert second.metadata["request_sent"] is False      # the pause held
    assert second.metadata["http_status"] is None        # nothing sent, no status invented
    assert second.metadata["suppressed_by_cooldown"] is True
    assert second.metadata["source_status"] == "TEMPORARILY_UNAVAILABLE"
    assert "error page" in second.title
    assert hits == 1 and runs[1][1]["cooldown_skips"] == 1


def test_require_login_json_is_auth_required_never_not_found(tmp_path):
    body = '{"message":"","require_login":true,"status":"fail"}'
    runs, _ = restricted_scan(tmp_path, lambda r: web.Response(status=401, text=body,
                                                                content_type="application/json"))
    finding = only(runs[0][0][0])
    assert finding.metadata["source_status"] == "AUTH_REQUIRED"
    assert finding.metadata["blocked"] == "auth_required"
    assert "sign-in" in finding.title


def test_challenge_and_checkpoint_are_challenge_required(tmp_path):
    for body in ('{"message":"challenge_required","status":"fail"}',
                 '{"message":"checkpoint_required","checkpoint_url":"/challenge/","status":"fail"}'):
        runs, _ = restricted_scan(tmp_path, lambda r, b=body: web.Response(status=400, text=b))
        finding = only(runs[0][0][0])
        assert finding.metadata["source_status"] == "CHALLENGE_REQUIRED"
        assert finding.metadata["outcome"] == "access_restricted"
        tmp_path = tmp_path / "next"
        tmp_path.mkdir()


def test_please_wait_is_rate_limited_and_the_pause_persists_across_runs(tmp_path):
    body = '{"message":"Please wait a few minutes before you try again.","require_login":true,"status":"fail"}'
    runs, hits = restricted_scan(tmp_path, lambda r: web.Response(status=401, text=body), runs=2)
    first = only(runs[0][0][0])
    assert first.metadata["outcome"] == "rate_limited"
    assert first.metadata["source_status"] == "RATE_LIMITED"
    assert first.metadata["cooldown_until"]
    assert "asked to wait" in first.title
    second = only(runs[1][0][0])
    assert second.metadata["request_sent"] is False
    assert hits == 1


def test_feedback_required_is_rate_limited(tmp_path):
    runs, _ = restricted_scan(tmp_path, lambda r: web.Response(status=400, text='{"message":"feedback_required"}'))
    assert only(runs[0][0][0]).metadata["source_status"] == "RATE_LIMITED"


def test_a_plain_403_is_unavailable_never_not_found(tmp_path):
    runs, _ = restricted_scan(tmp_path, lambda r: web.Response(status=403, text="Forbidden"))
    finding = only(runs[0][0][0])
    assert finding.metadata["outcome"] == "access_restricted"
    assert finding.metadata["source_status"] == "TEMPORARILY_UNAVAILABLE"


def test_empty_and_malformed_answers_are_unavailable_never_not_found(tmp_path):
    for body in ("", '{"data": {"user": ', "null"):
        runs, _ = restricted_scan(tmp_path, lambda r, b=body: web.Response(status=200, text=b))
        finding = only(runs[0][0][0])
        assert finding.status == MatchStatus.ERROR
        assert finding.metadata["outcome"] == "unknown"
        assert finding.metadata["source_status"] == "TEMPORARILY_UNAVAILABLE"
        tmp_path = tmp_path / "next"
        tmp_path.mkdir()


def test_a_timeout_is_a_network_error_and_retry_eligible(tmp_path):
    async def slow(request):
        await asyncio.sleep(2)
        return web.Response(text=profile_page("someone"))

    runs, _ = restricted_scan(tmp_path, slow, request_timeout=0.3)
    finding = only(runs[0][0][0])
    assert finding.metadata["outcome"] == "network_error"
    assert finding.metadata["source_status"] == "TEMPORARILY_UNAVAILABLE"
    assert finding.metadata["retry_eligible"] is True


def test_instagram_is_asked_again_once_the_pause_is_over(tmp_path):
    answers = iter([web.Response(text=ERROR_SHELL)])

    def handler(request):
        return next(answers, None) or web.Response(text=profile_page(request.match_info["user"]))

    async def go():
        async with Server(handler) as server:
            common = {"site_overrides": {"restrictions": production_restrictions()},
                      "cooldown_duration": 0.3, "max_cooldown_duration": 0.3}
            first, _ = await scan(server, tmp_path / "c.db", ["someone"], **common)
            await asyncio.sleep(0.5)
            second, _ = await scan(server, tmp_path / "c.db", ["someone"], **common)
            return first, second, server.instagram_hits()

    first, second, hits = asyncio.run(go())
    assert only(first[0]).metadata["source_status"] == "TEMPORARILY_UNAVAILABLE"
    recovered = only(second[0])
    assert recovered.metadata["source_status"] == "SUCCESS" and recovered.metadata["data_origin"] == "direct"
    assert hits == 2


def test_found_profile_carries_success_status_and_its_origin(tmp_path):
    async def go():
        async with Server(lambda r: web.Response(text=profile_page(r.match_info["user"]))) as server:
            first, _ = await scan(server, tmp_path / "c.db", ["someone"])
            # A second user searching the same name later: answered from the
            # shared result cache, nothing sent.
            second, metrics = await scan(server, tmp_path / "c.db", ["someone"])
            return first, second, server.instagram_hits(), metrics

    first, second, hits, metrics = asyncio.run(go())
    fresh, cached = only(first[0]), only(second[0])
    assert fresh.metadata["source_status"] == "SUCCESS"
    assert fresh.metadata["data_origin"] == "direct" and fresh.metadata["verification"] == "verified"
    assert fresh.metadata["retry_eligible"] is False
    assert cached.metadata["data_origin"] == "cache" and cached.metadata["cached"] is True
    assert hits == 1 and metrics["cache_hits"] == 1


def test_a_stale_profile_is_partial_and_unverified(tmp_path):
    db = tmp_path / "c.db"
    cache = ResponseCache(str(db))
    payload = {"outcome": "profile_found", "status": "probable", "http_status": 200, "decoy_check": "disabled"}
    cache.put_result(IG, "recent", payload, ttl_seconds=86400, collected_at=time.time() - 2 * 86400)
    cache.close()

    async def go():
        async with Server(lambda r: web.Response(text=ERROR_SHELL)) as server:
            results, _ = await scan(server, db, ["recent"],
                                    site_overrides={"restrictions": production_restrictions()})
            return results

    stale = only(asyncio.run(go())[0])
    assert stale.metadata["source_status"] == "PARTIAL"
    assert stale.metadata["data_origin"] == "stale_cache"
    assert stale.metadata["verification"] == "unverified"
    assert stale.metadata["unavailable_reason"] == "access_restricted"


def test_cancelling_a_search_mid_request_leaves_nothing_behind(tmp_path):
    async def go():
        gate = asyncio.Event()

        async def hang(request):
            gate.set()
            await asyncio.sleep(5)
            return web.Response(text=profile_page("someone"))

        async with Server(hang) as server:
            cache = ResponseCache(str(tmp_path / "c.db"))
            try:
                async with AsyncHttpClient(retries=0, cache=cache) as http:
                    plugin = UsernameSitesPlugin({"site_settings": {"Instagram": settings()}}, http)
                    plugin.sites = [instagram_site(server.base)]
                    task = asyncio.ensure_future(plugin.run(Identifier(value="someone", type=IdentifierType.USERNAME)))
                    await asyncio.wait_for(gate.wait(), 3)
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                    left_inflight = dict(plugin._inflight)
                stored = cache.get_result(IG, "someone")
            finally:
                cache.close()
            return left_inflight, stored

    left_inflight, stored = asyncio.run(go())
    assert left_inflight == {}     # a later search starts a fresh lookup
    assert stored is None          # nothing half-finished was cached


# -- third-party mirrors: labelled, and silent when they can't tell ----------

def mirror_site(base):
    return {"name": "Instagram (via Mirror)", "category": "social", "url": base + "/ig/{}/",
            "profile_url": "https://www.instagram.com/{}/", "check": "message",
            "found_text": ["alt=\"{} avatar\"", "@{} in Instagram"], "lowercase": True,
            "mirror_of": "Instagram", "found_only": True}


MIRROR_FOUND = ('<meta content="View latest posts and stories by @{name} in Instagram.">'
                '<img alt="{name} avatar"><span>Followers</span>')
MIRROR_PRIVATE = ('<meta content="View latest posts and stories by @{name} in Instagram.">'
                  '<img alt="{name} avatar">This profile is private')
MIRROR_UNKNOWN = '<meta content="View latest posts and stories by @{name} in Instagram.">verify you are not a bot'


def mirror_scan(handler, names):
    async def go():
        async with Server(handler) as server:
            async with AsyncHttpClient(retries=0) as http:
                plugin = UsernameSitesPlugin({"site_settings": {"Instagram (via Mirror)": {
                    "decoy_verification_enabled": False}}}, http)
                plugin.sites = [mirror_site(server.base)]
                return [await plugin.run(Identifier(value=n, type=IdentifierType.USERNAME)) for n in names]
    return asyncio.run(go())


def test_a_mirror_finding_is_labelled_third_party_and_links_to_the_platform():
    (found,), = mirror_scan(lambda r: web.Response(text=MIRROR_FOUND.format(name=r.match_info["user"])),
                            ["NatGeo"])
    assert found.status == MatchStatus.PROBABLE
    assert found.source_url == "https://www.instagram.com/natgeo/"
    assert found.metadata["mirror_of"] == "Instagram"
    assert found.metadata["data_origin"] == "third_party"
    assert found.metadata["verification"] == "unverified"
    assert found.metadata["checked_url"].endswith("/ig/natgeo/")


def test_a_private_profile_on_the_mirror_still_counts_as_found():
    (found,), = mirror_scan(lambda r: web.Response(text=MIRROR_PRIVATE.format(name=r.match_info["user"])),
                            ["someone"])
    assert found.status == MatchStatus.PROBABLE
    assert found.metadata["data_origin"] == "third_party"


def test_a_found_only_mirror_is_silent_when_it_cannot_tell():
    results = mirror_scan(lambda r: web.Response(text=MIRROR_UNKNOWN.format(name=r.match_info["user"])),
                          ["nobody"])
    assert results == [[]]
    # ...and when blocked or rate limited: never a "not found", never noise.
    assert mirror_scan(lambda r: web.Response(status=403, text="no"), ["nobody"]) == [[]]
    assert mirror_scan(lambda r: web.Response(status=429), ["nobody"]) == [[]]


def test_the_shipped_instagram_entry_checks_through_dumpor_and_confirms():
    import json
    from pathlib import Path
    sites = json.loads(Path(username_sites.DEFAULT_SITES_PATH).read_text(encoding="utf-8"))
    entries = [s for s in sites if s["name"] == "Instagram"]
    assert len(entries) == 1
    (instagram,) = entries
    assert instagram["url"].startswith("https://dumpor.io/")
    assert instagram["confirmed"] is True
    # Viewers show only accounts they have cached: an unanswered check is a
    # "couldn't tell" row linking to the viewer, not silence.
    assert "found_only" not in instagram and instagram["unanswered"] == "not_cached"
    assert [f["url"].split("/")[2] for f in instagram["fallbacks"]] == ["insta-stories-viewer.com", "storynavigation.com"]
    assert instagram["profile_url"] == "https://www.instagram.com/{}/"
    assert DEFAULT_CONFIG["sources"]["username_sites"]["site_settings"]["Instagram"]["max_concurrency"] == 1


def test_a_confirmed_mirror_shows_as_the_platform_and_keeps_its_origin_on_record():
    async def go():
        async with Server(lambda r: web.Response(text=MIRROR_FOUND.format(name=r.match_info["user"]))) as server:
            async with AsyncHttpClient(retries=0) as http:
                plugin = UsernameSitesPlugin({"site_settings": {"Instagram": settings()}}, http)
                site = dict(mirror_site(server.base), name="Instagram", confirmed=True)
                plugin.sites = [site]
                return await plugin.run(Identifier(value="natgeo", type=IdentifierType.USERNAME))

    (found,) = asyncio.run(go())
    assert found.status == MatchStatus.CONFIRMED
    assert found.source == "username_sites:Instagram"
    assert found.title == "Instagram profile found for 'natgeo'"
    assert found.metadata["verification"] == "verified"
    assert found.metadata["data_origin"] == "third_party"
    assert found.metadata["checked_url"].endswith("/ig/natgeo/")
