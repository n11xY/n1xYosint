"""Configuration loading (YAML/JSON) with environment-variable override support.

Config precedence (highest wins): CLI flags > environment variables > config file > defaults.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is a declared dependency
    yaml = None

DEFAULT_CONFIG: dict[str, Any] = {
    "concurrency": 20,
    "timeout_seconds": 10,
    "retries": 2,
    "retry_backoff": 1.5,
    "rate_limit_per_source": 5,   # max concurrent requests per source module
    "max_enrichment_identifiers": 200,  # safety cap on total identifiers across --depth rounds
    "user_agent": "n1xYosint/0.1 (+https://github.com/n11xY/n1xYosint) research use",
    "proxy": None,                # e.g. "socks5h://127.0.0.1:9050" for Tor on Kali
    "headers": {},
    "cache": {
        "enabled": True,
        "path": "~/.cache/n1xYosint/cache.sqlite3",
        "ttl_seconds": 86400,
    },
    "evidence": {
        "save_raw": False,
        "path": "~/.local/share/n1xYosint/evidence",
    },
    "logging": {
        "level": "INFO",
        "file": None,
    },
    "sources": {
        # per-source enable/disable + credentials; merged with plugin defaults
        "pastebin_search": {
            # psbdmp.ws has no DNS record as of this writing (confirmed via
            # multiple public resolvers) -- looks gone, not a transient
            # outage. Disabled by default so it doesn't produce a noisy
            # "request failed" error on every run; set true in your own
            # config.yaml to re-enable if the site ever comes back.
            "enabled": False,
        },
        "username_sites": {
            # Per-site overrides, keyed by the site's name in sites.json
            # (see username_sites.py). Instagram gets its own, deliberately
            # cautious request handling: it rate-limits or login-walls
            # anonymous traffic (hard from datacenter IPs), and every extra
            # request makes that worse. None of these numbers are Instagram's
            # limits -- it doesn't publish any -- they're conservative
            # defaults, all overridable in config.yaml.
            "site_settings": {
                "Instagram": {
                    "enabled": True,
                    "max_concurrency": 1,              # one Instagram request at a time
                    "request_timeout": 15,             # seconds
                    "minimum_request_interval": 5,     # seconds between request starts, shared across runs
                    "cooldown_duration": 900,          # after a 429/login wall with no Retry-After: 15 min,
                    "max_cooldown_duration": 21600,    # doubling per consecutive one, capped at 6h
                    "max_retry_attempts": 0,           # never re-ask within the same lookup
                    "cache_ttl": 86400,                # "profile found" reused for 24h
                    "negative_cache_ttl": 3600,        # "not found" reused for 1h
                    "stale_ttl": 604800,               # a found profile shown as stale for up to 7 more days
                                                       # while Instagram can't be re-checked
                    # Instagram's profile marker includes the username itself,
                    # so a decoy request can't catch anything it doesn't --
                    # it would only spend another request on the most
                    # rate-limited site in the database.
                    "decoy_verification_enabled": False,
                },
            },
        },
    },
    "plugins_dir": None,  # extra directory to load third-party plugins from
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _apply_env_overrides(cfg: dict[str, Any]) -> dict[str, Any]:
    """Allow OSINTRECON_<SOURCE>_API_KEY style env vars to populate source credentials."""
    prefix = "OSINTRECON_"
    for env_key, env_val in os.environ.items():
        if not env_key.startswith(prefix):
            continue
        rest = env_key[len(prefix):].lower()
        if rest.endswith("_api_key"):
            source_name = rest[: -len("_api_key")]
            cfg.setdefault("sources", {}).setdefault(source_name, {})["api_key"] = env_val
    return cfg


@dataclass
class Config:
    data: dict[str, Any] = field(default_factory=lambda: json.loads(json.dumps(DEFAULT_CONFIG)))

    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy of defaults
        if path:
            file_data = _load_file(Path(path))
            cfg = _deep_merge(cfg, file_data)
        cfg = _apply_env_overrides(cfg)
        return cls(data=cfg)

    def get(self, dotted_key: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def source_config(self, name: str) -> dict[str, Any]:
        return self.data.get("sources", {}).get(name, {})

    def is_source_enabled(self, name: str, default: bool = True) -> bool:
        return bool(self.source_config(name).get("enabled", default))


def _load_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        if yaml is None:
            raise RuntimeError("PyYAML is required to load YAML config files (pip install pyyaml)")
        return yaml.safe_load(text) or {}
    if path.suffix.lower() == ".json":
        return json.loads(text) or {}
    raise ValueError(f"Unsupported config format: {path.suffix}")
