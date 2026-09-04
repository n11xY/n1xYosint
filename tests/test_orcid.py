import asyncio

from osintrecon.core.models import Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.sources.orcid import OrcidPlugin


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

    async def get(self, source, url, headers=None, params=None):
        self.calls.append(params)
        return self._responses[len(self.calls) - 1]


def test_match_returns_finding_with_expected_fields():
    http = FakeHttp([FakeResp(data={"result": [{"orcid-identifier": {"path": "0000-0002-1992-2684"}}]})])
    plugin = OrcidPlugin(config={}, http=http)
    identifier = Identifier(value="Yann LeCun", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert len(http.calls) == 1
    assert len(findings) == 1
    finding = findings[0]
    assert finding.status == MatchStatus.UNCERTAIN
    assert finding.source == "orcid"
    assert finding.source_url == "https://orcid.org/0000-0002-1992-2684"
    assert finding.metadata["orcid_id"] == "0000-0002-1992-2684"


def test_no_match_makes_only_one_request_when_name_has_no_diacritics():
    http = FakeHttp([FakeResp(data={"result": []})])
    plugin = OrcidPlugin(config={}, http=http)
    identifier = Identifier(value="Zzzqqxx Nonexistentname", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert findings == []
    assert len(http.calls) == 1


def test_falls_back_to_ascii_folded_name_when_exact_form_finds_nothing():
    http = FakeHttp([
        FakeResp(data={"result": []}),
        FakeResp(data={"result": [{"orcid-identifier": {"path": "0000-1111-2222-3333"}}]}),
    ])
    plugin = OrcidPlugin(config={}, http=http)
    identifier = Identifier(value="Çağlar Öztürk", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert len(http.calls) == 2
    assert len(findings) == 1
    assert findings[0].metadata["matched_query"] == "Caglar Ozturk"


def test_single_word_name_makes_no_request():
    http = FakeHttp([])
    plugin = OrcidPlugin(config={}, http=http)
    identifier = Identifier(value="Madonna", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert findings == []
    assert http.calls == []


def test_request_error_returns_error_finding():
    http = FakeHttp([FakeResp(error="timeout")])
    plugin = OrcidPlugin(config={}, http=http)
    identifier = Identifier(value="Yann LeCun", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert len(findings) == 1
    assert findings[0].status == MatchStatus.ERROR


def test_non_200_status_returns_error_finding():
    http = FakeHttp([FakeResp(status=500, data={})])
    plugin = OrcidPlugin(config={}, http=http)
    identifier = Identifier(value="Yann LeCun", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert len(findings) == 1
    assert findings[0].status == MatchStatus.ERROR
    assert findings[0].metadata["http_status"] == 500
