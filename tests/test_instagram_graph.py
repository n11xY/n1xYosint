"""instagram_graph: Meta's Business Discovery API, against a fake client --
no request goes to Meta."""
import asyncio

from osintrecon.core.models import Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.sources.instagram_graph import InstagramGraphPlugin

TOKEN = "test-token-not-real"


class Resp:
    def __init__(self, status=200, data=None, error=None):
        self.status, self._data, self.error = status, data, error
        self.text = "" if data is None else "{}"
        self.cooldown_until = None

    def json(self):
        return self._data


class FakeHttp:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    async def get(self, source, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.resp


def run(resp, username="bluebottle", **config):
    http = FakeHttp(resp)
    plugin = InstagramGraphPlugin({"api_key": TOKEN, "ig_user_id": "17841405309211844", **config}, http)
    findings = asyncio.run(plugin.run(Identifier(value=username, type=IdentifierType.USERNAME)))
    return findings, http.calls


def error(code, subcode=None, message="x"):
    body = {"error": {"code": code, "message": message, "type": "OAuthException"}}
    if subcode is not None:
        body["error"]["error_subcode"] = subcode
    return body


def test_needs_both_a_token_and_an_account_id():
    assert not InstagramGraphPlugin({"api_key": TOKEN}, None).is_configured()
    assert not InstagramGraphPlugin({"ig_user_id": "1"}, None).is_configured()
    assert InstagramGraphPlugin({"api_key": TOKEN, "ig_user_id": "1"}, None).is_configured()


def test_a_professional_account_is_confirmed_from_the_official_api():
    findings, calls = run(Resp(200, {"business_discovery": {
        "username": "bluebottle", "name": "Blue Bottle Coffee", "biography": "Coffee",
        "website": "https://bluebottlecoffee.com", "followers_count": 400000, "media_count": 1200,
        "id": "17841401441775531"}, "id": "17841405309211844"}), username="BlueBottle")
    (finding,) = findings
    assert finding.status == MatchStatus.CONFIRMED
    assert finding.source_url == "https://www.instagram.com/bluebottle/"
    assert finding.metadata["source_status"] == "SUCCESS"
    assert finding.metadata["data_origin"] == "official_api"
    assert finding.metadata["verification"] == "verified"
    assert finding.metadata["followers_count"] == 400000
    url, kwargs = calls[0]
    assert url == "https://graph.facebook.com/v27.0/17841405309211844"
    assert kwargs["params"]["fields"].startswith("business_discovery.username(bluebottle){username,")
    # The token travels in a header only: never in the URL, params or cache.
    assert TOKEN not in url and TOKEN not in str(kwargs["params"])
    assert kwargs["headers"]["Authorization"] == f"OAuth {TOKEN}"
    assert kwargs["allow_cache"] is False


def test_cannot_find_user_is_no_finding_never_not_found():
    # Personal account, age-gated account or no account: the API can't tell.
    findings, _ = run(Resp(400, error(110, 2207013, "Cannot find User")))
    assert findings == []


def test_rate_limit_codes_are_rate_limited_and_retry_eligible():
    for code in (4, 17, 32, 613, 80002):
        (finding,), _ = run(Resp(400, error(code)))
        assert finding.status == MatchStatus.ERROR
        assert finding.metadata["source_status"] == "RATE_LIMITED"
        assert finding.metadata["retry_eligible"] is True
    (finding,), _ = run(Resp(429, None))
    assert finding.metadata["source_status"] == "RATE_LIMITED"


def test_an_expired_token_is_session_expired_and_an_invalid_one_auth_required():
    (expired,), _ = run(Resp(400, error(190, 463, "Session has expired")))
    assert expired.metadata["source_status"] == "SESSION_EXPIRED"
    assert expired.metadata["retry_eligible"] is False
    (invalid,), _ = run(Resp(400, error(190)))
    assert invalid.metadata["source_status"] == "AUTH_REQUIRED"
    (perm,), _ = run(Resp(403, error(10, message="Application does not have permission")))
    assert perm.metadata["source_status"] == "AUTH_REQUIRED"
    assert TOKEN not in str(perm.metadata)


def test_network_errors_and_odd_answers_are_never_results():
    (net,), _ = run(Resp(0, None, error="timeout"))
    assert net.metadata["source_status"] == "TEMPORARILY_UNAVAILABLE"
    (empty,), _ = run(Resp(200, None))
    assert empty.status == MatchStatus.ERROR and empty.metadata["source_status"] == "ERROR"
    (odd,), _ = run(Resp(200, {"id": "1"}))
    assert odd.metadata["source_status"] == "PARTIAL"
    (server,), _ = run(Resp(500, None))
    assert server.metadata["source_status"] == "ERROR"


def test_names_instagram_cannot_have_are_not_asked():
    findings, calls = run(Resp(200, {}), username="not a name!")
    assert findings == [] and calls == []
