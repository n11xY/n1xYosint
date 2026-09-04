"""Export subsystem -- JSON, CSV, TXT, HTML, and graph (JSON nodes/edges) report writers."""
from __future__ import annotations

import csv
import html
import json
from pathlib import Path

from osintrecon.core.engine import RunResult
from osintrecon.core.entity_scoring import confidence_bucket
from osintrecon.core.identity_graph import build_graph
from osintrecon.core.models import Finding, MatchStatus


def _finding_to_dict(f: Finding) -> dict:
    return {
        "finding_id": f.finding_id,
        "source": f.source,
        "category": f.category,
        "identifier_type": f.identifier.type.value,
        "identifier_value": f.identifier.value,
        "status": f.status.value,
        "confidence": f.confidence,
        "title": f.title,
        "source_url": f.source_url,
        "metadata": f.metadata,
        "discovered_identifiers": [
            {"type": d.type.value, "value": d.value} for d in f.discovered_identifiers
        ],
        "evidence_path": f.evidence_path,
        "timestamp": f.timestamp,
        "hop": f.hop,
    }


def export_json(result: RunResult, path: str) -> None:
    payload = {
        "stats": result.stats.to_dict(),
        "findings": [_finding_to_dict(f) for f in result.findings],
        "entities": [
            {
                "entity_id": e.entity_id,
                "identifiers": [{"type": i.type.value, "value": i.value} for i in e.identifiers],
                "finding_count": len(e.findings),
                "confidence": e.confidence,
                "reasons": e.reasons,
            }
            for e in result.entities
        ],
        "rejected_inputs": [{"raw": raw, "reason": reason} for raw, reason in result.rejected_inputs],
    }
    Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


CSV_FIELDS = [
    "finding_id", "source", "category", "identifier_type", "identifier_value",
    "status", "confidence", "title", "source_url", "timestamp", "hop",
]

# CSV formula injection (CWE-1236): a cell whose text starts with one of
# these characters is interpreted as a formula by Excel/Sheets when the
# file is opened, not as literal text (e.g. a search-result title of
# "=HYPERLINK(...)" or "@SUM(...)"). title/source_url come from live,
# attacker-influenced sources (any indexed web page controls its own
# title), and identifier_value can start with "=" too (EMAIL_RE permits
# it), so this needs handling at export time, not upstream.
_CSV_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def _sanitize_csv_cell(value):
    if isinstance(value, str) and value.startswith(_CSV_FORMULA_TRIGGERS):
        return "'" + value
    return value


def export_csv(result: RunResult, path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for f in result.findings:
            row = {k: _sanitize_csv_cell(v) for k, v in _finding_to_dict(f).items()}
            writer.writerow(row)


def export_txt(result: RunResult, path: str) -> None:
    lines = []
    lines.append("n1xYosint OSINT report")
    lines.append("=" * 40)
    lines.append("")
    lines.append("Execution statistics:")
    for key, value in result.stats.to_dict().items():
        lines.append(f"  {key}: {value}")
    lines.append("")

    by_identifier: dict[str, list[Finding]] = {}
    for f in result.findings:
        by_identifier.setdefault(f.identifier.value, []).append(f)

    for ident_value, findings in by_identifier.items():
        lines.append(f"Identifier: {ident_value}")
        lines.append("-" * 40)
        for f in sorted(findings, key=lambda x: -x.confidence):
            lines.append(f"  [{f.status.value.upper():9s} {f.confidence:.2f}] {f.source} ({f.category})")
            lines.append(f"    {f.title}")
            lines.append(f"    URL: {f.source_url}")
        lines.append("")

    if result.rejected_inputs:
        lines.append("Rejected inputs:")
        for raw, reason in result.rejected_inputs:
            lines.append(f"  {raw!r}: {reason}")

    Path(path).write_text("\n".join(lines), encoding="utf-8")


def export_graph(result: RunResult, path: str) -> None:
    """Writes the identity graph (nodes/edges derived from this run's
    correlated entities -- see core/identity_graph.py) as standalone JSON."""
    Path(path).write_text(json.dumps(build_graph(result), indent=2, ensure_ascii=False), encoding="utf-8")


_HTML_STATUS_CLASS = {
    MatchStatus.CONFIRMED: "status-confirmed",
    MatchStatus.PROBABLE: "status-probable",
    MatchStatus.UNCERTAIN: "status-uncertain",
    MatchStatus.ERROR: "status-error",
    MatchStatus.NOT_FOUND: "status-not-found",
}

_HTML_STYLE = """<style>
body { font-family: -apple-system, "Segoe UI", sans-serif; margin: 2rem; color: #1a1a1a; background: #fff; }
h1, h2, h3 { color: #111; }
table { border-collapse: collapse; width: 100%; margin-bottom: 1.5rem; }
th, td { border: 1px solid #ddd; padding: 0.4rem 0.6rem; text-align: left; font-size: 0.9rem; }
th { background: #f5f5f5; }
.status-confirmed { color: #0a7d2c; font-weight: bold; }
.status-probable { color: #a66a00; }
.status-uncertain { color: #888; }
.status-error { color: #c0392b; }
.status-not-found { color: #aaa; }
.entity { border: 1px solid #ccc; border-radius: 6px; padding: 0.8rem 1rem; margin-bottom: 1rem; }
.bucket-very-strong { border-color: #0a7d2c; }
.bucket-strong { border-color: #4a9c2c; }
.bucket-possible { border-color: #c99a00; }
.bucket-weak { border-color: #c07800; }
.bucket-very-weak { border-color: #999; }
</style>"""


def _esc(value) -> str:
    return html.escape(str(value), quote=True)


def _safe_href(url) -> str:
    # source_url originates from live third-party API/page data -- only
    # ever render it as a link when it's actually http(s), never let a
    # plugin-supplied string become a javascript:/data: URI.
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        return html.escape(url, quote=True)
    return "#"


def export_html(result: RunResult, path: str) -> None:
    by_category: dict[str, list[Finding]] = {}
    for f in result.findings:
        by_category.setdefault(f.category, []).append(f)

    parts: list[str] = [
        '<!doctype html><html><head><meta charset="utf-8">',
        "<title>n1xYosint OSINT report</title>",
        _HTML_STYLE,
        "</head><body>",
        "<h1>n1xYosint OSINT report</h1>",
        "<h2>Execution statistics</h2><table class=\"stats\">",
    ]
    for key, value in result.stats.to_dict().items():
        parts.append(f"<tr><th>{_esc(key.replace('_', ' '))}</th><td>{_esc(value)}</td></tr>")
    parts.append("</table>")

    for category, items in sorted(by_category.items()):
        parts.append(f"<h2>{_esc(category)}</h2>")
        parts.append(
            "<table class=\"findings\"><tr><th>Identifier</th><th>Source</th>"
            "<th>Status</th><th>Confidence</th><th>Title</th><th>URL</th></tr>"
        )
        for f in sorted(items, key=lambda x: -x.confidence):
            status_class = _HTML_STATUS_CLASS.get(f.status, "")
            parts.append(
                "<tr>"
                f"<td>{_esc(f.identifier.value)}</td>"
                f"<td>{_esc(f.source)}</td>"
                f'<td class="{status_class}">{_esc(f.status.value)}</td>'
                f"<td>{f.confidence:.2f}</td>"
                f"<td>{_esc(f.title)}</td>"
                f'<td><a href="{_safe_href(f.source_url)}">{_esc(f.source_url)}</a></td>'
                "</tr>"
            )
        parts.append("</table>")

    multi_entities = [e for e in result.entities if len(e.identifiers) > 1]
    if multi_entities:
        parts.append("<h2>Correlated entities</h2>")
        for e in sorted(multi_entities, key=lambda x: -x.confidence):
            bucket = confidence_bucket(e.confidence)
            identifiers_line = ", ".join(
                f"{i.type.value}:{i.value}" for i in sorted(e.identifiers, key=lambda i: (i.type.value, i.value))
            )
            sources_line = ", ".join(sorted({f.source for f in e.findings}))
            parts.append(f'<div class="entity bucket-{_esc(bucket.replace(" ", "-"))}">')
            parts.append(f"<h3>Entity {_esc(e.entity_id)} -- {_esc(bucket)} ({e.confidence:.0%})</h3>")
            parts.append(f"<p><strong>Identifiers:</strong> {_esc(identifiers_line)}</p>")
            if e.reasons:
                parts.append("<p><strong>Evidence:</strong></p><ul>")
                for reason in e.reasons:
                    parts.append(f"<li>{_esc(reason)}</li>")
                parts.append("</ul>")
            parts.append(f"<p><strong>Sources:</strong> {_esc(sources_line)}</p>")
            parts.append("</div>")

    if result.rejected_inputs:
        parts.append("<h2>Rejected inputs</h2><ul>")
        for raw, reason in result.rejected_inputs:
            parts.append(f"<li>{_esc(raw)}: {_esc(reason)}</li>")
        parts.append("</ul>")

    parts.append("</body></html>")
    Path(path).write_text("\n".join(parts), encoding="utf-8")


EXPORTERS = {
    "json": export_json,
    "csv": export_csv,
    "txt": export_txt,
    "html": export_html,
    "graph": export_graph,
}


def export(result: RunResult, path: str, fmt: str) -> None:
    if fmt not in EXPORTERS:
        raise ValueError(f"Unsupported export format: {fmt} (choose from {list(EXPORTERS)})")
    EXPORTERS[fmt](result, path)
