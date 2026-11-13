import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from osintrecon.core.models import Identifier, IdentifierType, MatchStatus
from osintrecon.core.timeline import account_dates
from osintrecon.plugins.sources.nexusmods import NexusModsPlugin


class _Http:
    def __init__(self, status=200, payload=None, error=None):
        self.status, self.payload, self.error = status, payload, error
        self.calls = []

    async def request(self, source, method, url, json_body=None, **kwargs):
        self.calls.append((source, method, url, json_body))
        return SimpleNamespace(status=self.status, error=self.error, json=lambda: self.payload)


def _run(http, name):
    return asyncio.run(NexusModsPlugin(config={}, http=http).run(Identifier(value=name, type=IdentifierType.USERNAME)))


def test_a_real_account_is_confirmed_with_its_join_date():
    http = _Http(payload={"data": {"userByName": {"memberId": 1, "name": "Dark0ne",
                                                  "joined": "2003-07-15T17:01:46Z",
                                                  "avatar": "https://avatars.nexusmods.com/1/100"}}})
    [finding] = _run(http, "dark0ne")
    assert finding.status is MatchStatus.CONFIRMED
    assert finding.source_url == "https://next.nexusmods.com/profile/Dark0ne"
    assert finding.metadata["avatar_url"].startswith("https://avatars.nexusmods.com/")
    # The join date lands on the account timeline.
    [dated] = account_dates([finding])
    assert dated.created.year == 2003
    source, method, _url, body = http.calls[0]
    assert (source, method) == ("nexusmods", "POST") and body["variables"] == {"name": "dark0ne"}


def test_no_such_user_is_no_finding_and_an_api_failure_is_an_error():
    assert _run(_Http(payload={"data": {"userByName": None}}), "nobody123") == []
    [failed] = _run(_Http(payload={"errors": [{"message": "boom"}], "data": {"userByName": None}}), "x")
    assert failed.status is MatchStatus.ERROR
    [down] = _run(_Http(status=503, payload=None), "x")
    assert down.status is MatchStatus.ERROR


def test_twitch_is_checked_through_the_helix_backed_lookup():
    sites = json.loads(Path("config/sites.json").read_text(encoding="utf-8"))
    sites = sites if isinstance(sites, list) else sites.get("sites", list(sites.values()))
    [twitch] = [s for s in sites if s["name"] == "Twitch"]
    assert twitch["url"].startswith("https://decapi.me/twitch/id/")
    assert twitch["profile_url"] == "https://www.twitch.tv/{}"
    assert twitch["found_pattern"] and twitch["not_found_text"] == "Invalid Twitch username"
