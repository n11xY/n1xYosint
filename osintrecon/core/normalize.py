"""Input normalization and validation layer.

Accepts raw strings from CLI args, files, or interactive prompts and turns
them into validated `Identifier` objects. Invalid entries are reported but
do not abort the whole batch.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from osintrecon.core.models import Identifier, IdentifierType

EMAIL_RE = re.compile(r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+$")
# Conservative: letters, digits, dot, underscore, hyphen, 1-39 chars (covers most platforms)
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,39}$")


@dataclass
class NormalizationResult:
    identifiers: list[Identifier]
    rejected: list[tuple[str, str]]  # (raw_value, reason)


def normalize_email(raw: str) -> str:
    return raw.strip().lower()


def normalize_username(raw: str) -> str:
    return raw.strip().lstrip("@")


def classify(raw: str) -> IdentifierType | None:
    value = raw.strip()
    if not value:
        return None
    if "@" in value:
        return IdentifierType.EMAIL
    return IdentifierType.USERNAME


def validate_and_build(raw: str) -> tuple[Identifier | None, str | None]:
    """Returns (Identifier, None) on success or (None, reason) on failure."""
    kind = classify(raw)
    if kind is None:
        return None, "empty input"

    if kind == IdentifierType.EMAIL:
        norm = normalize_email(raw)
        if not EMAIL_RE.match(norm):
            return None, f"invalid email format: {raw!r}"
        return Identifier(value=norm, type=IdentifierType.EMAIL, raw=raw), None

    norm = normalize_username(raw)
    if not USERNAME_RE.match(norm):
        return None, f"invalid username format: {raw!r}"
    return Identifier(value=norm, type=IdentifierType.USERNAME, raw=raw), None


def normalize_batch(raw_values: list[str]) -> NormalizationResult:
    identifiers: list[Identifier] = []
    rejected: list[tuple[str, str]] = []
    seen: set[tuple[IdentifierType, str]] = set()

    for raw in raw_values:
        ident, reason = validate_and_build(raw)
        if ident is None:
            rejected.append((raw, reason or "unknown error"))
            continue
        key = (ident.type, ident.value)
        if key in seen:
            continue  # silent de-dup of exact repeats within input
        seen.add(key)
        identifiers.append(ident)

    return NormalizationResult(identifiers=identifiers, rejected=rejected)


def load_from_file(path: str) -> list[str]:
    """Load one identifier per line from a text file, ignoring blanks and '#' comments."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    lines = []
    for line in p.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(stripped)
    return lines
