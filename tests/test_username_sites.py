import asyncio
from types import SimpleNamespace

from osintrecon.core.models import Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.sources.username_sites import UsernameSitesPlugin, _decoy_username, _is_found

STATUS_SITE = {"name": "Example", "url": "https://example.com/{}", "check": "status", "found_status": 200}
CONTENT_SITE = {
    "name": "Example",
    "url": "https://example.com/{}",
    "check": "content",
    "not_found_text": "This page doesn't exist",
}


def _resp(status: int, text: str = "") -> SimpleNamespace:
    return SimpleNamespace(status=status, text=text)


def test_status_check_found():
    assert _is_found(_resp(200), STATUS_SITE) is True


def test_status_check_not_found():
    assert _is_found(_resp(404), STATUS_SITE) is False


def test_content_check_found_when_not_found_text_absent():
    assert _is_found(_resp(200, "Welcome to alice's profile"), CONTENT_SITE) is True


def test_content_check_not_found_when_marker_present():
    assert _is_found(_resp(200, "Oops -- This page doesn't exist"), CONTENT_SITE) is False


def test_content_check_not_found_on_error_status():
    # Even without the not-found marker, a >=400 status is never a match.
    assert _is_found(_resp(500, "Welcome to alice's profile"), CONTENT_SITE) is False


def test_content_check_with_no_marker_configured_never_matches():
    # A misconfigured site (missing not_found_text) shouldn't silently
    # report every page as found.
    site = {"name": "Example", "url": "https://example.com/{}", "check": "content"}
    assert _is_found(_resp(200, "anything"), site) is False


def test_decoy_username_is_short_and_synthetic():
    decoy = _decoy_username()
    assert decoy.startswith("nx1")
    assert len(decoy) <= 15  # fits under the tightest per-site length cap
    assert decoy.isalnum()


def test_decoy_username_is_fresh_each_call():
    assert _decoy_username() != _decoy_username()


class FakeResp:
    def __init__(self, status=404):
        self.status = status
        self.error = None
        self.text = ""
        self.cached = False
        self.evidence_path = None

    def json(self):
        return None


class FakeHttp:
    def __init__(self):
        self.requested_urls = []

    async def get(self, source, url, headers=None, expected_statuses=None):
        self.requested_urls.append(url)
        return FakeResp(status=404)  # not-found for everything -- we only care about the URL built


def test_identifier_value_is_url_encoded_before_formatting():
    # Some site templates (config/sites.json) put the identifier directly in
    # the URL's hostname, e.g. "https://{}.itch.io" -- an unencoded value
    # containing a URL-structural character could change which host the
    # actual request goes to. quote(value, safe="") must neutralize that
    # before it ever reaches str.format().
    http = FakeHttp()
    plugin = UsernameSitesPlugin(config={}, http=http)
    plugin.sites = [{"name": "Evil", "url": "https://{}.itch.io", "check": "status", "found_status": 200}]

    asyncio.run(plugin.run(Identifier(value="evil.example.com#", type=IdentifierType.USERNAME)))

    assert len(http.requested_urls) == 1
    requested_host_and_beyond = http.requested_urls[0].split("://", 1)[1]
    assert requested_host_and_beyond.startswith("evil.example.com%23")
    assert "#" not in http.requested_urls[0]


class LoginWallHttp:
    """Every request lands on the platform's sign-in page, as Instagram does
    for anonymous requests from datacenter IPs."""

    def __init__(self):
        self.requested_urls = []

    async def get(self, source, url, headers=None, expected_statuses=None):
        self.requested_urls.append(url)
        resp = FakeResp(status=200)
        resp.url = "https://www.instagram.com/accounts/login/?next=%2Fsomeone%2F"
        resp.text = "<title>Login • Instagram</title>"
        return resp


def test_login_wall_redirect_is_reported_as_blocked_not_as_a_result():
    http = LoginWallHttp()
    plugin = UsernameSitesPlugin(config={}, http=http)
    plugin.sites = [{
        "name": "Instagram", "url": "https://www.instagram.com/{}/", "check": "message",
        "not_found_text": "<title>Instagram</title>", "login_wall": "/accounts/login",
    }]

    findings = asyncio.run(plugin.run(Identifier(value="someone", type=IdentifierType.USERNAME)))

    assert len(findings) == 1
    assert findings[0].status == MatchStatus.ERROR
    assert findings[0].metadata["blocked"] == "login_wall"
    assert findings[0].source_url == "https://www.instagram.com/someone/"
    assert len(http.requested_urls) == 1  # no decoy request spent on it


class PageHttp:
    def __init__(self, status, text):
        self.status, self.text = status, text
        self.requested_urls = []

    async def get(self, source, url, headers=None, expected_statuses=None):
        self.requested_urls.append(url)
        resp = FakeResp(status=self.status)
        resp.url = url
        resp.text = self.text
        return resp


FOUND_TEXT_SITE = {
    "name": "Instagram", "url": "https://www.instagram.com/{}/", "check": "message",
    "found_text": "Instagram photos and videos",
}


def test_found_text_site_reports_an_unrecognisable_page_as_unreadable_not_as_absent():
    # Instagram's anonymous "httpErrorPage" shell, served identically for
    # real and nonexistent accounts.
    http = PageHttp(200, "<title>Instagram</title> ... \"pageID\":\"httpErrorPage\"")
    plugin = UsernameSitesPlugin(config={}, http=http)
    plugin.sites = [FOUND_TEXT_SITE]

    findings = asyncio.run(plugin.run(Identifier(value="someone", type=IdentifierType.USERNAME)))

    assert len(findings) == 1
    assert findings[0].status == MatchStatus.ERROR
    assert findings[0].metadata["blocked"] == "unreadable_page"
    assert len(http.requested_urls) == 1


def test_found_text_site_matches_on_the_profile_marker():
    assert _is_found(_resp(200, "<title>Cristiano (@cristiano) • Instagram photos and videos</title>"), FOUND_TEXT_SITE)
    assert not _is_found(_resp(200, "<title>Instagram</title>"), FOUND_TEXT_SITE)
    assert not _is_found(_resp(404, "Instagram photos and videos"), FOUND_TEXT_SITE)


def test_found_text_site_with_a_known_not_found_marker_stays_quiet():
    site = {**FOUND_TEXT_SITE, "not_found_text": "Sorry, this page isn't available."}
    http = PageHttp(200, "Sorry, this page isn't available.")
    plugin = UsernameSitesPlugin(config={}, http=http)
    plugin.sites = [site]

    findings = asyncio.run(plugin.run(Identifier(value="someone", type=IdentifierType.USERNAME)))

    assert findings == []
