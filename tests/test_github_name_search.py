import asyncio

from osintrecon.core.models import Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.sources.github_name_search import GitHubNameSearchPlugin


class FakeResp:
    def __init__(self, status=200, data=None, error=None):
        self.status = status
        self.error = error
        self.evidence_path = None
        self._data = data

    def json(self):
        return self._data


class FakeHttp:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def get(self, source, url, headers=None, params=None, expected_statuses=None):
        self.calls.append(params)
        return self._responses[len(self.calls) - 1]


def _user(login="torvalds"):
    return {"login": login, "html_url": f"https://github.com/{login}", "avatar_url": "https://avatars/x"}


def test_match_returns_finding_with_discovered_username():
    http = FakeHttp([FakeResp(data={"items": [_user()]})])
    plugin = GitHubNameSearchPlugin(config={}, http=http)
    identifier = Identifier(value="Linus Torvalds", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert len(findings) == 1
    finding = findings[0]
    assert finding.status == MatchStatus.UNCERTAIN
    assert finding.discovered_identifiers == [Identifier(value="torvalds", type=IdentifierType.USERNAME)]


def test_falls_back_to_ascii_folded_name_when_exact_form_finds_nothing():
    http = FakeHttp([
        FakeResp(data={"items": []}),
        FakeResp(data={"items": [_user()]}),
    ])
    plugin = GitHubNameSearchPlugin(config={}, http=http)
    identifier = Identifier(value="Çağlar Öztürk", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert len(http.calls) == 2
    assert len(findings) == 1


def test_rate_limited_returns_error_finding():
    http = FakeHttp([FakeResp(status=403)])
    plugin = GitHubNameSearchPlugin(config={}, http=http)
    identifier = Identifier(value="Linus Torvalds", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert len(findings) == 1
    assert findings[0].status == MatchStatus.ERROR


def test_request_error_returns_error_finding():
    http = FakeHttp([FakeResp(error="timeout")])
    plugin = GitHubNameSearchPlugin(config={}, http=http)
    identifier = Identifier(value="Linus Torvalds", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert len(findings) == 1
    assert findings[0].status == MatchStatus.ERROR


def test_quick_search_depth_caps_results_below_normal():
    http = FakeHttp([FakeResp(data={"items": [_user(f"user{i}") for i in range(8)]})])
    plugin = GitHubNameSearchPlugin(config={"search_depth": "quick"}, http=http)
    identifier = Identifier(value="Linus Torvalds", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert len(findings) == 2


def test_deep_search_depth_allows_more_results_than_normal():
    http = FakeHttp([FakeResp(data={"items": [_user(f"user{i}") for i in range(8)]})])
    plugin = GitHubNameSearchPlugin(config={"search_depth": "deep"}, http=http)
    identifier = Identifier(value="Linus Torvalds", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert len(findings) == 8


def test_omitted_search_depth_matches_todays_default_normal_cap():
    http = FakeHttp([FakeResp(data={"items": [_user(f"user{i}") for i in range(8)]})])
    plugin = GitHubNameSearchPlugin(config={}, http=http)
    identifier = Identifier(value="Linus Torvalds", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert len(findings) == 5
