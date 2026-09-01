from osintrecon.output.exporters import _sanitize_csv_cell


def test_formula_trigger_chars_get_quoted():
    for trigger in ("=1+1", "+1", "-1", "@SUM(1)", "\ttab", "\rcr"):
        assert _sanitize_csv_cell(trigger) == "'" + trigger


def test_normal_text_is_untouched():
    assert _sanitize_csv_cell("GitHub profile found for 'alice'") == "GitHub profile found for 'alice'"
    assert _sanitize_csv_cell("https://github.com/alice") == "https://github.com/alice"


def test_non_string_values_are_untouched():
    assert _sanitize_csv_cell(0.85) == 0.85
    assert _sanitize_csv_cell(2) == 2
    assert _sanitize_csv_cell(None) is None
