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


class UsernameHttp:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    async def get(self, source, url, params=None):
        self.calls.append((url, params))
        return self.resp


def test_username_finds_the_global_wikimedia_account():
    http = UsernameHttp(FakeResp(data={"query": {"globaluserinfo": {
        "home": "enwiki", "name": "Jane Doe", "registration": "2008-04-12T16:22:49Z", "editcount": 120,
        "merged": [
            {"wiki": "enwiki", "url": "https://en.wikipedia.org", "editcount": 100},
            {"wiki": "commonswiki", "url": "https://commons.wikimedia.org", "editcount": 20},
            {"wiki": "dewiki", "url": "https://de.wikipedia.org", "editcount": 0},
        ],
    }}}))
    plugin = WikipediaPlugin(config={}, http=http)

    findings = asyncio.run(plugin.run(Identifier(value="Jane_Doe", type=IdentifierType.USERNAME)))

    assert http.calls[0][0] == "https://meta.wikimedia.org/w/api.php"
    assert http.calls[0][1]["guiuser"] == "Jane_Doe"
    assert len(findings) == 1
    f = findings[0]
    assert f.status == MatchStatus.CONFIRMED
    assert f.source_url == "https://meta.wikimedia.org/wiki/Special:CentralAuth/Jane_Doe"
    assert f.metadata["home_wiki"] == "enwiki"
    assert f.metadata["wikis_attached"] == 3
    assert f.metadata["wikis_edited"] == 2
    assert [w["wiki"] for w in f.metadata["top_wikis"]] == ["enwiki", "commonswiki"]
    assert f.metadata["locked"] is False


def test_username_without_an_account_returns_nothing():
    http = UsernameHttp(FakeResp(data={"batchcomplete": "", "query": {"globaluserinfo": {"missing": ""}}}))
    plugin = WikipediaPlugin(config={}, http=http)

    assert asyncio.run(plugin.run(Identifier(value="Zq9f8e7d6c", type=IdentifierType.USERNAME))) == []


def test_username_lookup_blocked_is_an_error_not_a_miss():
    plugin = WikipediaPlugin(config={}, http=UsernameHttp(FakeResp(status=403)))

    findings = asyncio.run(plugin.run(Identifier(value="Someone", type=IdentifierType.USERNAME)))

    assert [f.status for f in findings] == [MatchStatus.ERROR]
