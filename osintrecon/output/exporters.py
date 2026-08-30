"""Export subsystem -- JSON, CSV, and TXT report writers."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from osintrecon.core.engine import RunResult
from osintrecon.core.models import Finding


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


def export_csv(result: RunResult, path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for f in result.findings:
            row = _finding_to_dict(f)
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


EXPORTERS = {
    "json": export_json,
    "csv": export_csv,
    "txt": export_txt,
}


def export(result: RunResult, path: str, fmt: str) -> None:
    if fmt not in EXPORTERS:
        raise ValueError(f"Unsupported export format: {fmt} (choose from {list(EXPORTERS)})")
    EXPORTERS[fmt](result, path)
