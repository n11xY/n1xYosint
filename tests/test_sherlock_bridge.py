from osintrecon.plugins.sources.sherlock_bridge import _substitute


def test_substitute_in_plain_string():
    assert _substitute("https://example.com/{}", "alice") == "https://example.com/alice"


def test_substitute_in_nested_dict():
    payload = {"username": "{}", "meta": {"query": "name={}"}}
    result = _substitute(payload, "bob")
    assert result == {"username": "bob", "meta": {"query": "name=bob"}}


def test_substitute_in_list():
    assert _substitute(["{}", "static", {"k": "{}"}], "carol") == ["carol", "static", {"k": "carol"}]


def test_substitute_leaves_non_string_values_alone():
    assert _substitute({"count": 5, "flag": True, "name": "{}"}, "dave") == {
        "count": 5, "flag": True, "name": "dave",
    }
