from osintrecon.plugins.sources.speedrun_com import _linked_username


def test_extracts_username_from_link():
    assert _linked_username({"uri": "https://www.twitch.tv/gmhikaru"}) == "gmhikaru"


def test_extracts_username_with_trailing_slash():
    assert _linked_username({"uri": "https://www.youtube.com/@bluvsth/"}) == "@bluvsth"


def test_none_link_returns_empty():
    assert _linked_username(None) == ""


def test_empty_dict_returns_empty():
    assert _linked_username({}) == ""
