"""Progress events for a running scan, for a caller that wants to show the
scan as it happens (the web UI's live view) instead of only its result.

The engine calls the `progress` callable it was given with plain dicts:

* ``{"event": "round", "hop": 0, "checks": [[identifier, source], ...]}`` --
  every check this enrichment round will make, before any of them starts.
  ``source`` is the name the check's findings will carry: a plugin name
  ("github") or, for the site database, "username_sites:<site>".
* ``{"event": "check", "hop": 0, "identifier": ..., "source": ...,
  "outcome": ...}`` -- one check finished. ``outcome`` is one of OUTCOMES.
* ``{"event": "phase", "phase": "correlating"}`` -- every check is done and
  the findings are being deduplicated, scored and linked.

Every planned check gets exactly one "check" event, even when its plugin
crashes, so a progress view always reaches its total. A callback that
raises is logged and ignored: showing progress must never break a scan.
"""
from __future__ import annotations

from typing import Callable, Optional

from osintrecon.core.models import Finding, MatchStatus

ProgressCallback = Callable[[dict], None]

# Strongest first: a check with several findings reports the strongest.
OUTCOMES = ("found", "probable", "uncertain", "rate_limited", "error", "none")


def outcome_of(findings: list[Finding]) -> str:
    """What one check established, from the findings it returned."""
    statuses = {f.status for f in findings}
    if MatchStatus.CONFIRMED in statuses:
        return "found"
    if MatchStatus.PROBABLE in statuses:
        return "probable"
    if MatchStatus.UNCERTAIN in statuses:
        return "uncertain"
    errors = [f for f in findings if f.status is MatchStatus.ERROR]
    if errors:
        if any((f.metadata or {}).get("http_status") == 429
               or (f.metadata or {}).get("outcome") == "rate_limited" for f in errors):
            return "rate_limited"
        return "error"
    return "none"


def safe_emit(callback: Optional[ProgressCallback], event: dict, log) -> None:
    if callback is None:
        return
    try:
        callback(event)
    except Exception as exc:  # noqa: BLE001 -- a broken progress view must not break the scan
        log.debug("progress callback failed on %s: %s", event.get("event"), exc)
