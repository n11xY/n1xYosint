import asyncio

from osintrecon.core.models import Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.sources.openalex import OpenAlexPlugin


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


def _author(orcid_url="https://orcid.org/0000-0002-1992-2684"):
    return {
        "id": "https://openalex.org/A1969205032",
        "display_name": "Yann LeCun",
        "works_count": 477,
        "cited_by_count": 254927,
        "summary_stats": {"h_index": 120},
        "affiliations": [{"institution": {"display_name": "Supélec", "country_code": "FR"}}],
        "orcid": orcid_url,
    }


def test_match_returns_finding_with_expected_metadata_and_discovers_orcid():
    http = FakeHttp([FakeResp(data={"results": [_author()]})])
    plugin = OpenAlexPlugin(config={}, http=http)
    identifier = Identifier(value="Yann LeCun", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert len(findings) == 1
    finding = findings[0]
    assert finding.status == MatchStatus.UNCERTAIN
    assert finding.metadata["works_count"] == 477
    assert finding.metadata["h_index"] == 120
    assert finding.metadata["institution"] == "Supélec"
    assert finding.metadata["country"] == "FR"
    assert len(finding.discovered_identifiers) == 1
    assert finding.discovered_identifiers[0].type == IdentifierType.URL
    assert finding.discovered_identifiers[0].value == "https://orcid.org/0000-0002-1992-2684"


def test_no_linked_orcid_discovers_nothing():
    http = FakeHttp([FakeResp(data={"results": [_author(orcid_url=None)]})])
    plugin = OpenAlexPlugin(config={}, http=http)
    identifier = Identifier(value="Yann LeCun", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert findings[0].discovered_identifiers == []


def test_falls_back_to_ascii_folded_name_when_exact_form_finds_nothing():
    http = FakeHttp([
        FakeResp(data={"results": []}),
        FakeResp(data={"results": [_author()]}),
    ])
    plugin = OpenAlexPlugin(config={}, http=http)
    identifier = Identifier(value="Çağlar Öztürk", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert len(http.calls) == 2
    assert len(findings) == 1


def test_no_match_returns_empty_list():
    http = FakeHttp([FakeResp(data={"results": []})])
    plugin = OpenAlexPlugin(config={}, http=http)
    identifier = Identifier(value="Zzzqqxx Nonexistentname", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert findings == []
    assert len(http.calls) == 1


def test_request_error_returns_error_finding():
    http = FakeHttp([FakeResp(error="timeout")])
    plugin = OpenAlexPlugin(config={}, http=http)
    identifier = Identifier(value="Yann LeCun", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert len(findings) == 1
    assert findings[0].status == MatchStatus.ERROR
