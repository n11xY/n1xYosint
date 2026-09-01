"""SQLite-backed cache for HTTP responses, keyed by (source, url, method).

Avoids re-hitting rate-limited or slow sources on repeated runs against the
same identifiers. TTL is configurable; expired entries are treated as misses.
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
"""


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

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
