import asyncio

from osintrecon.core.models import Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.sources.wikipedia import WikipediaPlugin


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

    async def get(self, source, url, params=None):
        self.calls.append(params)
        return self._responses[len(self.calls) - 1]


def _opensearch_response(titles, urls, descriptions=None):
    descriptions = descriptions if descriptions is not None else [""] * len(titles)
    return ["query", titles, descriptions, urls]


def test_match_returns_finding_with_expected_fields():
    http = FakeHttp([FakeResp(data=_opensearch_response(
        ["Linus Torvalds"], ["https://en.wikipedia.org/wiki/Linus_Torvalds"], ["Finnish-American software engineer"],
    ))])
    plugin = WikipediaPlugin(config={}, http=http)
    identifier = Identifier(value="Linus Torvalds", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert len(findings) == 1
    finding = findings[0]
    assert finding.status == MatchStatus.UNCERTAIN
    assert finding.source_url == "https://en.wikipedia.org/wiki/Linus_Torvalds"
    assert finding.metadata["description"] == "Finnish-American software engineer"


def test_no_match_returns_empty_list():
    http = FakeHttp([FakeResp(data=_opensearch_response([], []))])
    plugin = WikipediaPlugin(config={}, http=http)
    identifier = Identifier(value="Zzzqqxx Nonexistentname", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert findings == []
    assert len(http.calls) == 1


def test_falls_back_to_ascii_folded_name_when_exact_form_finds_nothing():
    http = FakeHttp([
        FakeResp(data=_opensearch_response([], [])),
        FakeResp(data=_opensearch_response(["Caglar Ozturk"], ["https://en.wikipedia.org/wiki/X"])),
    ])
    plugin = WikipediaPlugin(config={}, http=http)
    identifier = Identifier(value="Çağlar Öztürk", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert len(http.calls) == 2
    assert len(findings) == 1


def test_request_error_returns_error_finding():
    http = FakeHttp([FakeResp(error="timeout")])
    plugin = WikipediaPlugin(config={}, http=http)
    identifier = Identifier(value="Linus Torvalds", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert len(findings) == 1
    assert findings[0].status == MatchStatus.ERROR


def test_non_200_status_returns_error_finding():
    http = FakeHttp([FakeResp(status=500)])
    plugin = WikipediaPlugin(config={}, http=http)
    identifier = Identifier(value="Linus Torvalds", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert len(findings) == 1
    assert findings[0].status == MatchStatus.ERROR


def test_quick_search_depth_requests_a_smaller_limit_than_normal():
    http = FakeHttp([FakeResp(data=_opensearch_response(["X"], ["https://en.wikipedia.org/wiki/X"]))])
    plugin = WikipediaPlugin(config={"search_depth": "quick"}, http=http)
    identifier = Identifier(value="Linus Torvalds", type=IdentifierType.NAME)

    asyncio.run(plugin.run(identifier))

    assert http.calls[0]["limit"] == 1


def test_deep_search_depth_requests_a_larger_limit_than_normal():
    http = FakeHttp([FakeResp(data=_opensearch_response(["X"], ["https://en.wikipedia.org/wiki/X"]))])
    plugin = WikipediaPlugin(config={"search_depth": "deep"}, http=http)
    identifier = Identifier(value="Linus Torvalds", type=IdentifierType.NAME)

    asyncio.run(plugin.run(identifier))

    assert http.calls[0]["limit"] == 6


def test_omitted_search_depth_matches_todays_default_normal_limit():
    http = FakeHttp([FakeResp(data=_opensearch_response(["X"], ["https://en.wikipedia.org/wiki/X"]))])
    plugin = WikipediaPlugin(config={}, http=http)
    identifier = Identifier(value="Linus Torvalds", type=IdentifierType.NAME)

    asyncio.run(plugin.run(identifier))

    assert http.calls[0]["limit"] == 3
