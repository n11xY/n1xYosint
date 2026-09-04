import json

from osintrecon.core.engine import RunResult
from osintrecon.core.identity_graph import build_graph
from osintrecon.core.models import Entity, Finding, Identifier, IdentifierType, MatchStatus, RunStats
from osintrecon.output.exporters import _sanitize_csv_cell, export_graph, export_html


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


def _sample_result():
    name_id = Identifier(value="Jane Doe", type=IdentifierType.NAME)
    user_id = Identifier(value="janedoe", type=IdentifierType.USERNAME)
    finding = Finding(
        source="github_name_search",
        identifier=name_id,
        status=MatchStatus.UNCERTAIN,
        source_url="https://github.com/janedoe",
        title='Account with <script>alert(1)</script> in the title',
        category="code-hosting",
    )
    entity = Entity(identifiers={name_id, user_id}, findings=[finding], confidence=0.4, reasons=["Linked by 2 independent sources"])
    return RunResult(findings=[finding], entities=[entity], stats=RunStats())


def test_export_html_escapes_finding_content_and_does_not_crash_on_empty_result(tmp_path):
    path = tmp_path / "report.html"
    export_html(_sample_result(), str(path))

    text = path.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;" in text

    empty_path = tmp_path / "empty.html"
    export_html(RunResult(), str(empty_path))
    assert "<html>" in empty_path.read_text(encoding="utf-8")


def test_export_graph_matches_build_graph_output(tmp_path):
    result = _sample_result()
    path = tmp_path / "graph.json"
    export_graph(result, str(path))

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written == build_graph(result)
    assert any(n["type"] == "Person" for n in written["nodes"])
