import asyncio
import json
from pathlib import Path
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


class ApiHttp:
    """200 for the real account, 404 for the decoy."""

    def __init__(self):
        self.requested_urls = []

    async def get(self, source, url, headers=None, expected_statuses=None):
        self.requested_urls.append(url)
        resp = FakeResp(status=404 if "nx1" in url else 200)
        resp.url = url
        return resp


def test_profile_url_is_the_link_shown_for_an_api_checked_site():
    http = ApiHttp()
    plugin = UsernameSitesPlugin(config={}, http=http)
    plugin.sites = [{
        "name": "Example", "url": "https://api.example.com/users/{}", "check": "status",
        "found_status": 200, "not_found_status": 404, "profile_url": "https://example.com/@{}",
    }]

    findings = asyncio.run(plugin.run(Identifier(value="alice", type=IdentifierType.USERNAME)))

    assert len(findings) == 1
    assert findings[0].status == MatchStatus.CONFIRMED
    assert findings[0].source_url == "https://example.com/@alice"
    assert findings[0].metadata["checked_url"] == "https://api.example.com/users/alice"
    assert http.requested_urls[0] == "https://api.example.com/users/alice"


def test_site_database_entries_are_well_formed_and_unique():
    sites = json.loads((Path(__file__).resolve().parents[1] / "config" / "sites.json").read_text(encoding="utf-8"))
    names = [s["name"].lower() for s in sites]
    assert len(names) == len(set(names))
    for site in sites:
        assert site["url"].count("{}") == 1, site["name"]
        assert site["check"] in ("status", "message"), site["name"]
        if "profile_url" in site:
            assert site["profile_url"].count("{}") == 1, site["name"]
        if site["check"] == "message":
            assert site.get("not_found_text") or site.get("found_text"), site["name"]


class HeaderHttp:
    def __init__(self):
        self.headers = []

    async def get(self, source, url, headers=None, expected_statuses=None):
        self.headers.append(headers)
        resp = FakeResp(status=404 if "nx1" in url else 200)
        resp.url = url
        return resp


def test_browser_headers_can_be_turned_off_per_site():
    http = HeaderHttp()
    plugin = UsernameSitesPlugin(config={}, http=http)
    plugin.sites = [
        {"name": "Plain", "url": "https://a.example/{}", "check": "status", "found_status": 200,
         "not_found_status": 404, "browser_headers": False},
        {"name": "Browser", "url": "https://b.example/{}", "check": "status", "found_status": 200,
         "not_found_status": 404},
    ]

    findings = asyncio.run(plugin.run(Identifier(value="alice", type=IdentifierType.USERNAME)))

    assert len(findings) == 2
    assert None in http.headers  # the client's own User-Agent for "Plain" (and its decoy)
    assert any(h and "Chrome" in h["User-Agent"] for h in http.headers)


def test_lastfm_challenge_page_is_unreadable_not_a_found_account():
    from osintrecon.plugins.sources.username_sites import LookupOutcome, _classify

    sites = json.loads((Path(__file__).resolve().parents[1] / "config" / "sites.json").read_text(encoding="utf-8"))
    site = next(s for s in sites if s["name"] == "Last.fm")
    challenge = SimpleNamespace(status=200, text="<title>Client Challenge</title>", error=None, url="")
    profile = SimpleNamespace(status=200, error=None, url="",
                              text='<link rel="canonical" href="https://www.last.fm/user/Alice" data-x />')

    assert _classify(challenge, site, "Alice") is LookupOutcome.UNKNOWN
    assert _classify(profile, site, "Alice") is LookupOutcome.PROFILE_FOUND
    assert _classify(SimpleNamespace(status=404, text="", error=None, url=""), site, "Alice") is LookupOutcome.NOT_FOUND


def test_adult_sites_are_skipped_unless_included():
    sites = json.loads((Path(__file__).resolve().parents[1] / "config" / "sites.json").read_text(encoding="utf-8"))
    adult = {s["name"] for s in sites if s.get("nsfw")}
    assert adult and all(s["category"] == "adult" for s in sites if s.get("nsfw"))

    default = {s["name"] for s in UsernameSitesPlugin(config={}, http=FakeHttp()).sites}
    included = {s["name"] for s in UsernameSitesPlugin(config={"include_adult": True}, http=FakeHttp()).sites}

    assert not default & adult
    assert adult <= included
    assert included - adult == default


def test_include_adult_flag_reaches_username_sites_config():
    from osintrecon.cli import build_parser

    args = build_parser().parse_args(["-u", "alice", "--include-adult"])
    assert args.include_adult is True


def test_redirect_to_a_not_found_url_is_not_found_even_on_a_challenge_page():
    from osintrecon.plugins.sources.username_sites import LookupOutcome, _classify
    site = {"name": "Bandcamp", "url": "https://{}.bandcamp.com", "check": "message",
            "found_text": "\"url\":\"https://{}.bandcamp.com", "not_found_url": "bandcamp.com/signup?new_domain="}
    free = SimpleNamespace(status=200, text="<title>Client Challenge</title>", error=None,
                           url="https://bandcamp.com/signup?new_domain=nobody1")
    real = SimpleNamespace(status=200, text='{"url":"https://subpop.bandcamp.com"}', error=None,
                           url="https://subpop.bandcamp.com/")
    challenged = SimpleNamespace(status=200, text="<title>Client Challenge</title>", error=None,
                                 url="https://subpop.bandcamp.com/")
    assert _classify(free, site, "nobody1") is LookupOutcome.NOT_FOUND
    assert _classify(real, site, "subpop") is LookupOutcome.PROFILE_FOUND
    assert _classify(challenged, site, "subpop") is LookupOutcome.UNKNOWN


def test_site_headers_are_added_to_the_request():
    from osintrecon.plugins.sources.username_sites import _BROWSER_HEADERS, _headers_for
    youtube = {"name": "YouTube", "headers": {"Cookie": "SOCS=CAI"}}
    assert _headers_for(youtube) == {**_BROWSER_HEADERS, "Cookie": "SOCS=CAI"}
    assert _headers_for({"name": "Plain", "browser_headers": False, "headers": {"X-A": "1"}}) == {"X-A": "1"}
    assert _headers_for({"name": "Plain", "browser_headers": False}) is None


def test_a_403_challenge_is_never_read_as_not_found():
    from osintrecon.plugins.sources.username_sites import LookupOutcome, _classify
    status_site = {"name": "Example", "url": "https://example.com/{}", "check": "status",
                   "found_status": 200, "not_found_status": 404}
    challenge = SimpleNamespace(status=403, text="<title>Just a moment...</title>", error=None, url="")
    assert _classify(challenge, status_site, "someone") is LookupOutcome.ACCESS_RESTRICTED
    assert _classify(challenge, CONTENT_SITE, "someone") is LookupOutcome.ACCESS_RESTRICTED
    # unless the site itself answers a free name with that status
    forbids_missing = dict(status_site, not_found_status=403)
    assert _classify(challenge, forbids_missing, "someone") is LookupOutcome.NOT_FOUND


class _ProfilePage:
    """Answers the real name with a profile page, the decoy with a 404."""

    def __init__(self, body):
        self.body = body

    async def get(self, source, url, headers=None, expected_statuses=None):
        status = 404 if "nx1" in url else 200
        return SimpleNamespace(status=status, text="" if status == 404 else self.body, error=None, cached=False,
                               evidence_path=None, url=url, headers={"Content-Type": "text/html"},
                               json=lambda: None)


def test_a_found_profile_carries_what_its_page_says():
    page = ('<script type="application/ld+json">{"@type":"Person","name":"Alice Liddell",'
            '"url":"https://example.com/alice","sameAs":["https://twitter.com/liddell"]}</script>'
            '<a rel="me" href="https://alice.example">site</a>')
    plugin = UsernameSitesPlugin(config={}, http=_ProfilePage(page))
    plugin.sites = [{"name": "Example", "url": "https://example.com/{}", "check": "status", "found_status": 200}]

    [finding] = asyncio.run(plugin.run(Identifier(value="alice", type=IdentifierType.USERNAME)))

    assert finding.metadata["name"] == "Alice Liddell"
    assert finding.metadata["linked_accounts"][0]["platform"] == "twitter"
    assert {(i.type.value, i.value) for i in finding.discovered_identifiers} == {
        ("username", "liddell"), ("url", "https://alice.example")}
