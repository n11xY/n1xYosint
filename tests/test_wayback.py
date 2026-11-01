import asyncio

from osintrecon.core.models import Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.sources.wayback import WaybackPlugin

HEADER = ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"]


class FakeResp:
    def __init__(self, status=200, rows=None):
        self.status = status
        self.error = None
        self.evidence_path = None
        self._rows = rows

    def json(self):
        return self._rows


class FakeHttp:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def get(self, source, url, params=None, expected_statuses=None):
        self.calls.append(params)
        return FakeResp(rows=self.rows)


def _run(rows, value="cristiano", config=None):
    http = FakeHttp(rows)
    plugin = WaybackPlugin(config=config or {}, http=http)
    findings = asyncio.run(plugin.run(Identifier(value=value, type=IdentifierType.USERNAME)))
    return findings, http


def test_archived_instagram_profile_is_reported_as_uncertain_with_latest_capture():
    rows = [HEADER,
            ["com,instagram)/cristiano", "20260804092432", "https://www.instagram.com/cristiano/", "text/html", "200", "x", "1"],
            ["com,instagram)/cristiano", "20261002210025", "https://www.instagram.com/cristiano/", "text/html", "200", "y", "1"]]
    findings, http = _run(rows)

    assert len(findings) == 1
    f = findings[0]
    assert f.status == MatchStatus.UNCERTAIN
    assert f.source == "wayback:Instagram"
    assert f.source_url == "https://web.archive.org/web/20261002210025/https://www.instagram.com/cristiano/"
    assert f.metadata["last_archived"] == "2026-10-02"
    assert http.calls[0]["url"] == "instagram.com/cristiano/"
    assert http.calls[0]["limit"] == -25  # newest captures, not the oldest


def test_never_archived_profile_produces_nothing():
    findings, _ = _run([])
    assert findings == []


def test_username_cannot_add_path_segments_to_the_archived_url():
    _, http = _run([], value="a/../b")
    assert http.calls[0]["url"] == "instagram.com/a%2F..%2Fb/"
