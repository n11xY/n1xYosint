"""Input normalization and validation layer.

Accepts raw strings from CLI args, files, or interactive prompts and turns
them into validated `Identifier` objects. Invalid entries are reported but
do not abort the whole batch.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import phonenumbers

from osintrecon.core.models import Identifier, IdentifierType

EMAIL_RE = re.compile(r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+$")
# Conservative: letters, digits, dot, underscore, hyphen, 1-39 chars (covers most platforms)
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,39}$")
# Phone *candidates* only -- "+", digits, and common human separators (space,
# dash, dot, parens). Requires a leading "+" so a plain numeric username
# (e.g. a Steam ID) never gets misclassified as a phone number: without a
# country code a bare digit string is genuinely ambiguous, so we don't guess.
PHONE_CANDIDATE_RE = re.compile(r"^\+[\d\s\-.()]{7,20}$")
# A full name: 2+ space-separated words, each starting with a letter
# (apostrophes/hyphens allowed inside a word for "O'Brien", "Anne-Marie").
# USERNAME_RE already rejects anything with a space, so this never takes a
# real username away from that branch -- it only classifies input that was
# always going to be rejected otherwise.
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z'\-]*(\s+[A-Za-z][A-Za-z'\-]*)+$")


@dataclass
class NormalizationResult:
    identifiers: list[Identifier]
    rejected: list[tuple[str, str]]  # (raw_value, reason)


def normalize_email(raw: str) -> str:
    return raw.strip().lower()


def normalize_username(raw: str) -> str:
    return raw.strip().lstrip("@")


def normalize_phone(raw: str) -> str:
    """E.164 form (e.g. "+15551234567"), the canonical shape every phone
    plugin/dedup check relies on -- so "+1 (555) 123-4567" and "+15551234567"
    are recognized as the same identifier."""
    parsed = phonenumbers.parse(raw.strip(), None)
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def normalize_name(raw: str) -> str:
    # Collapse internal whitespace only -- casing is left alone ("McDonald",
    # "O'Brien" are valid as typed; force-titlecasing would break them).
    return " ".join(raw.split())


def classify(raw: str) -> IdentifierType | None:
    value = raw.strip()
    if not value:
        return None
    if PHONE_CANDIDATE_RE.match(value):
        return IdentifierType.PHONE
    # A leading "@" is the @mention convention (Twitter/Instagram/etc.), not
    # an email separator -- "@some_user" has no domain before the "@" and
    # was never meant as one. Only classify as email when "@" shows up
    # *after* something, matching how an actual address is shaped.
    if "@" in value and not value.startswith("@"):
        return IdentifierType.EMAIL
    if NAME_RE.match(value):
        return IdentifierType.NAME
    return IdentifierType.USERNAME


def validate_and_build(raw: str) -> tuple[Identifier | None, str | None]:
    """Returns (Identifier, None) on success or (None, reason) on failure."""
    kind = classify(raw)
    if kind is None:
        return None, "empty input"

    if kind == IdentifierType.PHONE:
        try:
            norm = normalize_phone(raw)
        except phonenumbers.NumberParseException:
            return None, f"invalid phone number: {raw!r}"
        if not phonenumbers.is_valid_number(phonenumbers.parse(norm, None)):
            return None, f"invalid phone number: {raw!r}"
        return Identifier(value=norm, type=IdentifierType.PHONE, raw=raw), None

    if kind == IdentifierType.EMAIL:
        norm = normalize_email(raw)
        if not EMAIL_RE.match(norm):
            return None, f"invalid email format: {raw!r}"
        return Identifier(value=norm, type=IdentifierType.EMAIL, raw=raw), None

    if kind == IdentifierType.NAME:
        return Identifier(value=normalize_name(raw), type=IdentifierType.NAME, raw=raw), None

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
