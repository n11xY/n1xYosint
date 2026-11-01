"""SQLite-backed cache for HTTP responses, keyed by (source, url, method).

Avoids re-hitting rate-limited or slow sources on repeated runs against the
same identifiers. TTL is configurable; expired entries are treated as misses.

The same database file also holds two small pieces of state that have to
outlive a single run (each web search, for instance, is its own run):

- lookup_results: what a lookup *concluded* (e.g. "profile found"), for
  sources whose raw responses can't be cached safely -- Instagram's login
  wall and error shell are HTTP 200 pages that say nothing about the
  account, and replaying them for a day would be wrong. Each entry carries
  its own collection time and expiry, so a caller can tell a fresh entry
  from a stale one and decide what a stale one is still good for.
- source_state: a source's rate-limit cooldown, its run of consecutive 429s
  (for exponential backoff) and the next time a request may start (for
  minimum spacing between requests), shared by every run and every process
  using this file.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from osintrecon.core.logging_setup import get_logger

log = get_logger("cache")

SCHEMA = """
CREATE TABLE IF NOT EXISTS http_cache (
    cache_key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    status_code INTEGER,
    body TEXT,
    headers TEXT,
    fetched_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_http_cache_source ON http_cache(source);
CREATE TABLE IF NOT EXISTS lookup_results (
    namespace TEXT NOT NULL,
    lookup_key TEXT NOT NULL,
    payload TEXT NOT NULL,
    collected_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    PRIMARY KEY (namespace, lookup_key)
);
CREATE TABLE IF NOT EXISTS source_state (
    source TEXT PRIMARY KEY,
    cooldown_until REAL NOT NULL DEFAULT 0,
    consecutive_429 INTEGER NOT NULL DEFAULT 0,
    next_slot REAL NOT NULL DEFAULT 0
);
"""

_SOURCE_STATE_FIELDS = ("cooldown_until", "consecutive_429", "next_slot")


def _key(source: str, method: str, url: str, extra: str = "") -> str:
    raw = f"{source}:{method.upper()}:{url}:{extra}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class ResponseCache:
    def __init__(self, path: str, ttl_seconds: int = 86400, enabled: bool = True):
        self.enabled = enabled
        self.ttl_seconds = ttl_seconds
        self._conn: Optional[sqlite3.Connection] = None
        if enabled:
            resolved = Path(path).expanduser()
            resolved.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(resolved), check_same_thread=False)
            self._conn.executescript(SCHEMA)
            self._conn.commit()
            # The cache stores full response bodies -- breach records, profile
            # data, anything a source returned -- so it shouldn't be readable
            # by other accounts on a shared machine (this tool explicitly
            # targets Kali-style multi-tool boxes). chmod runs every time,
            # not just on first create, so it also self-heals a cache file
            # left world-readable by a version of this tool from before this
            # fix. No-op-ish on Windows (only the read-only bit applies there).
            try:
                os.chmod(resolved, 0o600)
            except OSError:
                pass

    def get(self, source: str, method: str, url: str, extra: str = "") -> Optional[dict[str, Any]]:
        if not self.enabled or self._conn is None:
            return None
        key = _key(source, method, url, extra)
        cur = self._conn.execute(
            "SELECT status_code, body, headers, fetched_at FROM http_cache WHERE cache_key = ?",
            (key,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        status_code, body, headers, fetched_at = row
        if time.time() - fetched_at > self.ttl_seconds:
            return None
        log.debug("cache hit: %s %s", source, url)
        return {
            "status_code": status_code,
            "body": body,
            "headers": json.loads(headers) if headers else {},
            "cached": True,
            "fetched_at": fetched_at,
        }

    def put(self, source: str, method: str, url: str, status_code: int, body: str,
             headers: dict[str, str], extra: str = "") -> None:
        if not self.enabled or self._conn is None:
            return
        key = _key(source, method, url, extra)
        self._conn.execute(
            "INSERT OR REPLACE INTO http_cache (cache_key, source, url, status_code, body, headers, fetched_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (key, source, url, status_code, body, json.dumps(dict(headers)), time.time()),
        )
        self._conn.commit()

    # -- classified lookup results --------------------------------------

    def get_result(self, namespace: str, key: str) -> Optional[dict[str, Any]]:
        """The stored result for (namespace, key), expired or not -- an
        expired one comes back with stale=True rather than as a miss, since
        a caller may still want to show it (clearly labelled) when the
        source can't be asked again right now."""
        if not self.enabled or self._conn is None:
            return None
        row = self._conn.execute(
            "SELECT payload, collected_at, expires_at FROM lookup_results WHERE namespace = ? AND lookup_key = ?",
            (namespace, key),
        ).fetchone()
        if row is None:
            return None
        payload, collected_at, expires_at = row
        try:
            data = json.loads(payload)
        except ValueError:
            return None
        return {
            "payload": data,
            "collected_at": collected_at,
            "expires_at": expires_at,
            "stale": time.time() >= expires_at,
        }

    def put_result(self, namespace: str, key: str, payload: dict[str, Any], ttl_seconds: float,
                   collected_at: Optional[float] = None) -> None:
        if not self.enabled or self._conn is None:
            return
        collected = time.time() if collected_at is None else collected_at
        self._conn.execute(
            "INSERT OR REPLACE INTO lookup_results (namespace, lookup_key, payload, collected_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (namespace, key, json.dumps(payload), collected, collected + ttl_seconds),
        )
        self._conn.commit()

    def invalidate_results(self, namespace: str, key: Optional[str] = None) -> int:
        """Drop one stored result, or every result in a namespace. Returns
        how many were removed."""
        if not self.enabled or self._conn is None:
            return 0
        if key is None:
            cur = self._conn.execute("DELETE FROM lookup_results WHERE namespace = ?", (namespace,))
        else:
            cur = self._conn.execute(
                "DELETE FROM lookup_results WHERE namespace = ? AND lookup_key = ?", (namespace, key)
            )
        self._conn.commit()
        return cur.rowcount

    # -- per-source request state ---------------------------------------

    def get_source_state(self, source: str) -> dict[str, Any]:
        state: dict[str, Any] = {"cooldown_until": 0.0, "consecutive_429": 0, "next_slot": 0.0}
        if not self.enabled or self._conn is None:
            return state
        row = self._conn.execute(
            "SELECT cooldown_until, consecutive_429, next_slot FROM source_state WHERE source = ?", (source,)
        ).fetchone()
        if row is not None:
            state.update(zip(_SOURCE_STATE_FIELDS, row))
        return state

    def update_source_state(self, source: str, **fields: Any) -> None:
        if not self.enabled or self._conn is None:
            return
        unknown = set(fields) - set(_SOURCE_STATE_FIELDS)
        if unknown:
            raise ValueError(f"unknown source_state field(s): {', '.join(sorted(unknown))}")
        self._conn.execute("INSERT OR IGNORE INTO source_state (source) VALUES (?)", (source,))
        if fields:
            assignments = ", ".join(f"{name} = ?" for name in fields)
            self._conn.execute(f"UPDATE source_state SET {assignments} WHERE source = ?", (*fields.values(), source))
        self._conn.commit()

    def reset_source_state(self, source: str) -> None:
        """Forget a source's cooldown, backoff and spacing state."""
        if not self.enabled or self._conn is None:
            return
        self._conn.execute("DELETE FROM source_state WHERE source = ?", (source,))
        self._conn.commit()

    def reserve_slot(self, source: str, interval: float) -> float:
        """Claim the next start time for a request to `source` and push the
        shared schedule `interval` seconds past it, atomically, so runs (and
        processes) sharing this file space their requests out between them
        instead of each keeping its own clock. Returns the claimed start time
        (epoch seconds), which is now if the schedule is free."""
        now = time.time()
        if not self.enabled or self._conn is None:
            return now
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute("SELECT next_slot FROM source_state WHERE source = ?", (source,)).fetchone()
            next_slot = row[0] if row else 0.0
            # A slot implausibly far ahead can only come from a clock change
            # or a crashed run -- never let it stall requests indefinitely.
            if next_slot - now > max(60.0, 10 * interval):
                next_slot = now
            slot = max(now, next_slot)
            self._conn.execute("INSERT OR IGNORE INTO source_state (source) VALUES (?)", (source,))
            self._conn.execute("UPDATE source_state SET next_slot = ? WHERE source = ?", (slot + interval, source))
            self._conn.commit()
        except sqlite3.Error:
            self._conn.rollback()
            raise
        return slot

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
