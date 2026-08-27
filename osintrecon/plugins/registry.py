"""Plugin discovery and instantiation.

Discovers built-in modules under `osintrecon.plugins.sources`, plus any
external plugins from a user-configured directory, and instantiates the
ones enabled in config (and, for API-key-gated sources, actually configured).
"""
from __future__ import annotations

import importlib
import importlib.util
import inspect
import pkgutil
import sys
from pathlib import Path
from typing import Optional

from osintrecon.core.config import Config
from osintrecon.core.http_client import AsyncHttpClient
from osintrecon.core.logging_setup import get_logger
from osintrecon.plugins.base import SourcePlugin
from osintrecon.plugins import sources as builtin_sources_pkg

log = get_logger("registry")


def _iter_plugin_classes_in_module(module) -> list[type[SourcePlugin]]:
    found = []
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if issubclass(obj, SourcePlugin) and obj is not SourcePlugin and obj.__module__ == module.__name__:
            found.append(obj)
    return found


def discover_builtin() -> list[type[SourcePlugin]]:
    classes: list[type[SourcePlugin]] = []
    for _, mod_name, _ in pkgutil.iter_modules(builtin_sources_pkg.__path__):
        full_name = f"{builtin_sources_pkg.__name__}.{mod_name}"
        module = importlib.import_module(full_name)
        classes.extend(_iter_plugin_classes_in_module(module))
    return classes


def discover_external(plugins_dir: Optional[str]) -> list[type[SourcePlugin]]:
    if not plugins_dir:
        return []
    directory = Path(plugins_dir).expanduser()
    if not directory.is_dir():
        log.warning("plugins_dir does not exist: %s", directory)
        return []
    classes: list[type[SourcePlugin]] = []
    for py_file in directory.glob("*.py"):
        spec = importlib.util.spec_from_file_location(f"n1xYosint_external_{py_file.stem}", py_file)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001 - external plugin, isolate failures
            log.error("failed to load external plugin %s: %s", py_file, exc)
            continue
        classes.extend(_iter_plugin_classes_in_module(module))
    return classes


class PluginRegistry:
    def __init__(self, config: Config, http: AsyncHttpClient):
        self.config = config
        self.http = http
        self._classes: list[type[SourcePlugin]] = []

    def discover(self) -> "PluginRegistry":
        self._classes = discover_builtin() + discover_external(self.config.get("plugins_dir"))
        return self

    def instantiate_enabled(self) -> list[SourcePlugin]:
        instances: list[SourcePlugin] = []
        for cls in self._classes:
            source_cfg = self.config.source_config(cls.name)
            if not self.config.is_source_enabled(cls.name, default=True):
                log.debug("source disabled by config: %s", cls.name)
                continue
            plugin = cls(source_cfg, self.http)
            if not plugin.is_configured():
                log.warning("skipping %s: requires an API key (set sources.%s.api_key)", cls.name, cls.name)
                continue
            instances.append(plugin)
        return instances

    def all_known(self) -> list[type[SourcePlugin]]:
        return list(self._classes)
