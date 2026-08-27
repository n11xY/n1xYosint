"""Generic search-engine module backed by a legitimate, key-authenticated
Web Search API (Bing Web Search API by default). Deliberately does NOT scrape
search-engine result pages, which typically forbid automated access in their
terms of service -- it only calls an official, licensed API endpoint.

Configure via:
  sources:
    search_api:
      enabled: true
      provider: bing        # currently supported: bing
      api_key: "<your key>"
      results_per_query: 10
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

BING_ENDPOINT = "https://api.bing.microsoft.com/v7.0/search"


class SearchAPIPlugin(SourcePlugin):
    name: ClassVar[str] = "search_api"
    category: ClassVar[str] = "search-engine"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME, IdentifierType.EMAIL}
    requires_api_key: ClassVar[bool] = True
    description: ClassVar[str] = "Queries a licensed web search API (Bing) for mentions of the identifier."

    async def run(self, identifier: Identifier) -> list[Finding]:
        provider = self.config.get("provider", "bing")
        if provider != "bing":
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url="", title=f"Unsupported search_api provider: {provider}", category=self.category,
            )]

        query = f'"{identifier.value}"'
        headers = {"Ocp-Apim-Subscription-Key": self.config["api_key"]}
        params = {"q": query, "count": str(self.config.get("results_per_query", 10))}

        resp = await self.http.get(self.name, BING_ENDPOINT, headers=headers, params=params)

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=BING_ENDPOINT, title="search API request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=BING_ENDPOINT, title=f"search API returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        data = resp.json() or {}
        web_pages = (data.get("webPages") or {}).get("value", [])
        findings = []
        for page in web_pages:
            findings.append(Finding(
                source=self.name,
                identifier=identifier,
                status=MatchStatus.UNCERTAIN,  # a keyword hit isn't proof of identity
                source_url=page.get("url", BING_ENDPOINT),
                title=page.get("name", "Search result"),
                category=self.category,
                metadata={"snippet": page.get("snippet"), "date_published": page.get("datePublished")},
                evidence_path=resp.evidence_path,
            ))
        return findings
