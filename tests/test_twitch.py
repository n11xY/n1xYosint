import asyncio
from types import SimpleNamespace

from osintrecon.core.models import Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.sources.twitch_api import TwitchAPIPlugin, _decapi_date


class _Decapi:
    """decapi.me as the keyless route sees it: one canned answer per path."""

    def __init__(self, answers):
        self.answers = answers
        self.urls = []

    async def get(self, source, url, **kwargs):
        self.urls.append(url)
        for part, (status, text) in self.answers.items():
            if part in url:
                return SimpleNamespace(status=status, text=text, error=None)
        return SimpleNamespace(status=404, text="", error=None)


def _run(http, name, config=None):
    return asyncio.run(TwitchAPIPlugin(config=config or {}, http=http).run(
        Identifier(value=name, type=IdentifierType.USERNAME)))


def test_without_an_app_twitch_answers_with_date_followers_and_photo():
    http = _Decapi({"creation/": (200, "Nov 3. 2012 - 03:50:32 PM (UTC)"),
                    "followcount/": (200, "11289374"),
                    "avatar/": (200, "https://static-cdn.jtvnw.net/x-profile_image-300x300.png")})
    [finding] = _run(http, "Shroud")
    assert finding.status is MatchStatus.CONFIRMED
    assert finding.source_url == "https://www.twitch.tv/shroud"
    assert finding.metadata["created_at"] == "2012-11-03T15:50:32Z"
    assert finding.metadata["followers"] == 11289374
    assert finding.metadata["profile_image_url"].startswith("https://static-cdn.jtvnw.net/")


def test_no_such_account_and_impossible_names_are_no_finding():
    assert _run(_Decapi({"creation/": (400, "Invalid Twitch username specified: qzxvab12zz")}), "qzxvab12zz") == []
    http = _Decapi({})
    assert _run(http, "ab") == [] and http.urls == []  # too short to be a Twitch login: not even asked


def test_a_lookup_that_fails_is_an_error_not_a_miss():
    [failed] = _run(_Decapi({"creation/": (503, "")}), "someone")
    assert failed.status is MatchStatus.ERROR
    [odd] = _run(_Decapi({"creation/": (200, "Something unexpected")}), "someone")
    assert odd.status is MatchStatus.ERROR


def test_decapi_dates():
    assert _decapi_date("May 1. 2021 - 02:12:49 AM (UTC)") == "2021-05-01T02:12:49Z"
    assert _decapi_date("not a date") is None
