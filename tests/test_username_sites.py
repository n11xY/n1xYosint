from types import SimpleNamespace

from osintrecon.plugins.sources.username_sites import _decoy_username, _is_found

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
