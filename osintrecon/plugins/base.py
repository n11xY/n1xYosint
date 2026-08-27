"""Common interface every source module (plugin) must implement.

Adding a new OSINT source means subclassing `SourcePlugin` and dropping the
module into `osintrecon/plugins/sources/` (or an external plugins directory
configured via `plugins_dir`) — the core engine never needs to change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from osintrecon.core.http_client import AsyncHttpClient
from osintrecon.core.models import Finding, Identifier, IdentifierType


class SourcePlugin(ABC):
    """Base class for all OSINT source modules.

    Subclasses declare `name`, `category`, and `accepts` (which identifier
    types they can handle), then implement `run()` to return zero or more
    `Finding`s for a single identifier.
    """

    name: ClassVar[str] = "base"
    category: ClassVar[str] = "general"
    accepts: ClassVar[set[IdentifierType]] = set()
    requires_api_key: ClassVar[bool] = False
    description: ClassVar[str] = ""

    def __init__(self, config: dict, http: AsyncHttpClient):
        self.config = config  # this plugin's section of the source config
        self.http = http

    def supports(self, identifier: Identifier) -> bool:
        return identifier.type in self.accepts

    def is_configured(self) -> bool:
        """Override for plugins that require an API key to function."""
        if not self.requires_api_key:
            return True
        return bool(self.config.get("api_key"))

    @abstractmethod
    async def run(self, identifier: Identifier) -> list[Finding]:
        """Query this source for the given identifier and return findings."""
        raise NotImplementedError
