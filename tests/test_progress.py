import asyncio

from osintrecon.core.engine import Engine
from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.core.progress import outcome_of
from osintrecon.plugins.base import SourcePlugin
from osintrecon.plugins.sources.username_sites import UsernameSitesPlugin

ALICE = Identifier(value="alice", type=IdentifierType.USERNAME)
MAIL = Identifier(value="a@example.org", type=IdentifierType.EMAIL)


def finding(source, status, identifier=ALICE, **metadata):
    return Finding(source=source, identifier=identifier, status=status, source_url="https://x/", metadata=metadata)


class Hit(SourcePlugin):
    name = "hit"
    accepts = {IdentifierType.USERNAME}

    async def run(self, identifier):
        return [finding("hit", MatchStatus.CONFIRMED, identifier)]


class Miss(SourcePlugin):
    name = "miss"
    accepts = {IdentifierType.USERNAME, IdentifierType.EMAIL}

    async def run(self, identifier):
        return []


class Boom(SourcePlugin):
    name = "boom"
    accepts = {IdentifierType.USERNAME}

    async def run(self, identifier):
        raise RuntimeError("broken plugin")


class Sites(SourcePlugin):
    """Reports two of its three planned checks itself, like username_sites."""
    name = "sites"
    accepts = {IdentifierType.USERNAME}

    def planned_checks(self, identifier):
        return ["sites:A", "sites:B", "sites:C"]

    async def run(self, identifier):
        a = finding("sites:A", MatchStatus.PROBABLE, identifier)
        self.report_check("sites:A", identifier, [a])
        self.report_check("sites:B", identifier, [])
        return [a, finding("sites:C", MatchStatus.ERROR, identifier, http_status=429)]


def run_round(plugins, identifiers):
    events = []
    engine = Engine(config=None, progress=events.append)
    for plugin in plugins:
        plugin.report_check = engine._report_check
    findings = asyncio.run(engine._run_round(identifiers, plugins, asyncio.Semaphore(5)))
    return engine, events, findings


def test_outcome_of_picks_the_strongest_signal():
    assert outcome_of([]) == "none"
    assert outcome_of([finding("x", MatchStatus.UNCERTAIN), finding("x", MatchStatus.CONFIRMED)]) == "found"
    assert outcome_of([finding("x", MatchStatus.ERROR, http_status=429)]) == "rate_limited"
    assert outcome_of([finding("x", MatchStatus.ERROR, outcome="rate_limited")]) == "rate_limited"
    assert outcome_of([finding("x", MatchStatus.ERROR)]) == "error"


def test_every_planned_check_gets_exactly_one_event():
    plugins = [Hit({}, None), Miss({}, None), Boom({}, None), Sites({}, None)]
    _, events, findings = run_round(plugins, [ALICE, MAIL])

    planned = events[0]
    assert planned["event"] == "round"
    assert sorted(map(tuple, planned["checks"])) == sorted([
        ("alice", "hit"), ("alice", "miss"), ("alice", "boom"),
        ("alice", "sites:A"), ("alice", "sites:B"), ("alice", "sites:C"),
        ("a@example.org", "miss"),
    ])
    checks = {(e["identifier"], e["source"]): e["outcome"] for e in events if e["event"] == "check"}
    assert len([e for e in events if e["event"] == "check"]) == len(planned["checks"])
    assert checks == {
        ("alice", "hit"): "found", ("alice", "miss"): "none", ("alice", "boom"): "error",
        ("alice", "sites:A"): "probable", ("alice", "sites:B"): "none", ("alice", "sites:C"): "rate_limited",
        ("a@example.org", "miss"): "none",
    }
    assert len(findings) == 3  # hit, sites:A, the sites:C error; nothing from the crash


def test_a_failing_progress_callback_never_breaks_the_scan():
    def broken(event):
        raise ValueError("view went away")

    engine = Engine(config=None, progress=broken)
    plugin = Hit({}, None)
    plugin.report_check = engine._report_check
    findings = asyncio.run(engine._run_round([ALICE], [plugin], asyncio.Semaphore(1)))
    assert [f.source for f in findings] == ["hit"]


def test_without_a_callback_nothing_is_tracked():
    engine = Engine(config=None)
    findings = asyncio.run(engine._run_round([ALICE], [Hit({}, None)], asyncio.Semaphore(1)))
    assert len(findings) == 1 and engine._reported == set()


class NoHttp:
    def set_source_policy(self, *args):
        pass

    async def get(self, source, url, headers=None, expected_statuses=None):
        class R:
            status, error, text, url, evidence_path = 404, None, "", "", None
        return R()


def test_username_sites_reports_each_enabled_site():
    plugin = UsernameSitesPlugin(config={"site_settings": {"Off": {"enabled": False}}}, http=NoHttp())
    plugin.sites = [
        {"name": "One", "url": "https://one.example/{}", "check": "status", "found_status": 200},
        {"name": "Two", "url": "https://two.example/{}", "check": "status", "found_status": 200},
        {"name": "Off", "url": "https://off.example/{}", "check": "status", "found_status": 200},
    ]
    seen = []
    plugin.report_check = lambda source, identifier, findings: seen.append((source, len(findings)))

    assert plugin.planned_checks(ALICE) == ["username_sites:One", "username_sites:Two"]
    asyncio.run(plugin.run(ALICE))
    assert sorted(seen) == [("username_sites:One", 0), ("username_sites:Two", 0)]
