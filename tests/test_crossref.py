import asyncio

from osintrecon.core.models import Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.sources.crossref import CrossrefPlugin


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


def _work(doi="10.1109/5.726791", title="Gradient-based learning applied to document recognition"):
    return {
        "title": [title],
        "DOI": doi,
        "container-title": ["Proceedings of the IEEE"],
        "published": {"date-parts": [[1998]]},
    }


def test_match_returns_finding_with_doi_url_and_metadata():
    http = FakeHttp([FakeResp(data={"message": {"items": [_work()]}})])
    plugin = CrossrefPlugin(config={}, http=http)
    identifier = Identifier(value="Yann LeCun", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert len(findings) == 1
    finding = findings[0]
    assert finding.status == MatchStatus.UNCERTAIN
    assert finding.source_url == "https://doi.org/10.1109/5.726791"
    assert finding.metadata["venue"] == "Proceedings of the IEEE"
    assert finding.metadata["year"] == 1998


def test_missing_doi_falls_back_to_api_url():
    http = FakeHttp([FakeResp(data={"message": {"items": [_work(doi=None)]}})])
    plugin = CrossrefPlugin(config={}, http=http)
    identifier = Identifier(value="Yann LeCun", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert findings[0].source_url == "https://api.crossref.org/works"


def test_item_without_title_is_skipped():
    work_no_title = _work()
    work_no_title["title"] = []
    http = FakeHttp([FakeResp(data={"message": {"items": [work_no_title, _work()]}})])
    plugin = CrossrefPlugin(config={}, http=http)
    identifier = Identifier(value="Yann LeCun", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert len(findings) == 1


def test_falls_back_to_ascii_folded_name_when_exact_form_finds_nothing():
    http = FakeHttp([
        FakeResp(data={"message": {"items": []}}),
        FakeResp(data={"message": {"items": [_work()]}}),
    ])
    plugin = CrossrefPlugin(config={}, http=http)
    identifier = Identifier(value="Çağlar Öztürk", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert len(http.calls) == 2
    assert len(findings) == 1


def test_no_match_returns_empty_list():
    http = FakeHttp([FakeResp(data={"message": {"items": []}})])
    plugin = CrossrefPlugin(config={}, http=http)
    identifier = Identifier(value="Zzzqqxx Nonexistentname", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert findings == []
    assert len(http.calls) == 1


def test_request_error_returns_error_finding():
    http = FakeHttp([FakeResp(error="timeout")])
    plugin = CrossrefPlugin(config={}, http=http)
    identifier = Identifier(value="Yann LeCun", type=IdentifierType.NAME)

    findings = asyncio.run(plugin.run(identifier))

    assert len(findings) == 1
    assert findings[0].status == MatchStatus.ERROR
