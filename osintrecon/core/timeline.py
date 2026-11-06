"""When was each found account created? Dates read from the platforms' own
API answers, so a result can be laid out as a footprint over time ("joined
GitHub in 2011, the chess sites in 2019").

Every source module stores what its API returns under its own key and in
its own format (ISO strings, Unix seconds, Lichess's milliseconds, dev.to's
"Jun 26, 2018"); this module is the one place that knows each of them, so
the plugins stay a plain record of what the platform said.

Only CONFIRMED and PROBABLE findings count: an UNCERTAIN hit may not be
the account at all. The keyserver's *unverified* keys never carry a date
here for the same reason (anyone can upload a key with any date).

A found account is not proof of who owns it: the same username on two
sites is often two people. So each dated account also says whether there
is evidence tying it to another found account -- a signed Keybase proof,
a profile linking to the other account, or the same profile photo -- and
which; one without is "same name only", and a view must say so.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from osintrecon.core.models import Finding, MatchStatus

# source -> (metadata key, what the date marks)
ACCOUNT_DATE_FIELDS: dict[str, tuple[str, str]] = {
    "github": ("created_at", "account"),
    "bluesky": ("created_at", "account"),
    "anilist": ("created_at", "account"),
    "chess_com": ("joined", "account"),
    "codeforces": ("registered_at", "account"),
    "devto": ("joined_at", "account"),
    "dockerhub": ("date_joined", "account"),
    "hackernews": ("created", "account"),
    "keybase": ("joined", "account"),
    "lichess": ("created_at", "account"),
    "mastodon_social": ("created_at", "account"),
    "scratch": ("joined", "account"),
    "speedrun_com": ("signup", "account"),
    "steam_api": ("account_created", "account"),
    "twitch_api": ("created_at", "account"),
    "twitter_api": ("created_at", "account"),
    "wikipedia": ("created_at", "account"),
    # A verified OpenPGP key's creation date: when the key was made, which
    # is not an account sign-up -- shown as "key created".
    "pgp_keys": ("created_at", "key"),
}

COUNTED = (MatchStatus.CONFIRMED, MatchStatus.PROBABLE)

# Nothing in this database predates the web; anything earlier is a parse
# error or a placeholder value (0, 1970-01-01), not a real sign-up.
_EARLIEST = datetime(1990, 1, 1, tzinfo=timezone.utc)
_TEXT_FORMATS = ("%b %d, %Y", "%B %d, %Y", "%a %b %d %H:%M:%S %z %Y")


# Platform names as plugins and the site database spell them, folded to
# one key per platform so "chess_com", "Chess.com" and a profile's link to
# chess.com all meet. Anything not listed folds by lowercasing and dropping
# punctuation ("username_sites:Docker Hub" -> "dockerhub").
_PLATFORM_ALIASES = {
    "mastodon_social": "mastodon", "steam_api": "steam", "twitch_api": "twitch",
    "twitter_api": "twitter", "youtube_api": "youtube", "wikipedia": "wikimedia",
}
EVIDENCE_KINDS = ("keybase_proof", "profile_link", "avatar_match")


def platform_key(name: str) -> str:
    name = name.split(":", 1)[1] if name.startswith("username_sites:") else name
    name = _PLATFORM_ALIASES.get(name, name)
    return re.sub(r"[^a-z0-9]", "", name.lower())


@dataclass(frozen=True)
class DatedAccount:
    source: str
    identifier: str
    identifier_type: str
    created: datetime
    kind: str            # "account" or "key"
    url: str
    status: str          # "confirmed" or "probable"
    # What ties this account to another found account, strongest first;
    # empty when nothing does (the same name is all they share).
    evidence: tuple[str, ...] = ()

    @property
    def linked(self) -> bool:
        return bool(self.evidence)


def evidence_links(findings: Iterable[Finding]) -> list[tuple[tuple[str, str], tuple[str, str], str]]:
    """Every piece of evidence tying two found accounts together, as
    (account, account, kind) with accounts keyed (platform key, lowercased
    identifier); each pair and kind once. Only accounts the search found
    (confirmed or probable) take part: a profile linking to an account the
    search didn't find links nothing."""
    findings = list(findings)
    found: dict[tuple[str, str], list[Finding]] = {}
    for f in findings:
        if f.status in COUNTED and f.source != "avatar_correlation":
            found.setdefault((platform_key(f.source), f.identifier.value.lower()), []).append(f)
    pairs: dict[tuple, None] = {}

    def link(a, b, kind):
        if a != b and a in found and b in found:
            first, second = sorted((a, b))
            pairs[(first, second, kind)] = None

    by_avatar: dict[str, tuple[str, str]] = {}
    for node, items in found.items():
        for f in items:
            meta = f.metadata or {}
            for target in meta.get("linked_accounts") or []:
                if isinstance(target, dict) and target.get("platform") and target.get("username"):
                    link(node, (platform_key(str(target["platform"])), str(target["username"]).lower()),
                         target.get("evidence") or "profile_link")
            if meta.get("avatar_url"):
                by_avatar.setdefault(meta["avatar_url"], node)
    for f in findings:
        if f.source == "avatar_correlation":
            meta = f.metadata or {}
            a, b = by_avatar.get(meta.get("avatar_url_a")), by_avatar.get(meta.get("avatar_url_b"))
            # Same platform on both sides is a shared default picture, not
            # one person (results saved before avatar_correlation knew).
            if a and b and a[0] != b[0]:
                link(a, b, "avatar_match")
    return list(pairs)


def account_links(findings: Iterable[Finding]) -> dict[tuple[str, str], tuple[str, ...]]:
    """(platform key, lowercased identifier) -> the kinds of evidence tying
    that found account to at least one other found account."""
    evidence: dict[tuple[str, str], set[str]] = {}
    for a, b, kind in evidence_links(findings):
        evidence.setdefault(a, set()).add(kind)
        evidence.setdefault(b, set()).add(kind)
    return {node: tuple(k for k in EVIDENCE_KINDS if k in kinds) for node, kinds in evidence.items()}


def parse_date(value: Any, now: Optional[datetime] = None) -> Optional[datetime]:
    """A UTC datetime from the shapes these APIs use, or None."""
    now = now or datetime.now(timezone.utc)
    parsed: Optional[datetime] = None
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and re.fullmatch(r"\d{9,13}(\.\d+)?", value.strip())):
        number = float(value)
        if number > 1e11:  # milliseconds (Lichess, AniList's newer fields)
            number /= 1000
        try:
            parsed = datetime.fromtimestamp(number, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        iso = text.replace("Z", "+00:00") if text.endswith("Z") else text
        try:
            parsed = datetime.fromisoformat(iso)
        except ValueError:
            for fmt in _TEXT_FORMATS:
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    if parsed < _EARLIEST or parsed > now + timedelta(days=1):
        return None
    return parsed


def account_dates(findings: Iterable[Finding], now: Optional[datetime] = None) -> list[DatedAccount]:
    """Every dated account among the findings, oldest first; one entry per
    (source, identifier), keeping the earliest date seen."""
    findings = list(findings)
    links = account_links(findings)
    best: dict[tuple[str, str], DatedAccount] = {}
    for f in findings:
        field = ACCOUNT_DATE_FIELDS.get(f.source)
        if field is None or f.status not in COUNTED:
            continue
        key, kind = field
        created = parse_date((f.metadata or {}).get(key), now)
        if created is None:
            continue
        entry = DatedAccount(
            source=f.source, identifier=f.identifier.value, identifier_type=f.identifier.type.value,
            created=created, kind=kind, url=f.source_url, status=f.status.value,
            evidence=links.get((platform_key(f.source), f.identifier.value.lower()), ()),
        )
        slot = (f.source, f.identifier.value)
        if slot not in best or created < best[slot].created:
            best[slot] = entry
    return sorted(best.values(), key=lambda d: (d.created, d.source))
