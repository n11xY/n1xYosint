import asyncio

from osintrecon.core.models import Identifier, IdentifierType
from osintrecon.plugins.sources.hackernews import HackerNewsPlugin


def test_username_with_dot_is_skipped_without_a_network_call():
    # "." is valid per the project's own USERNAME_RE but illegal in a
    # Firebase Realtime Database path -- hacker-news.firebaseio.com
    # returns HTTP 400 for it (confirmed live), even percent-encoded.
    # This must short-circuit before ever calling self.http, so pass
    # None as the http client to prove no request is attempted.
    plugin = HackerNewsPlugin(config={}, http=None)
    identifier = Identifier(value="john.doe", type=IdentifierType.USERNAME)

    findings = asyncio.run(plugin.run(identifier))

    assert findings == []


def test_normal_username_is_not_skipped():
    calls = []

    class FakeResp:
        status = 200
        error = None
        evidence_path = None

        def json(self):
            return None  # Firebase's real "not found" signal

    class FakeHttp:
        async def get(self, source, url):
            calls.append(url)
            return FakeResp()

    plugin = HackerNewsPlugin(config={}, http=FakeHttp())
    identifier = Identifier(value="pg", type=IdentifierType.USERNAME)

    findings = asyncio.run(plugin.run(identifier))

    assert calls == ["https://hacker-news.firebaseio.com/v0/user/pg.json"]
    assert findings == []
