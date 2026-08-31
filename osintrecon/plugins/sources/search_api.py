"""Generic search-engine module backed by a legitimate, key-authenticated
Web Search API. Deliberately does NOT scrape search-engine result pages,
which typically forbid automated access in their terms of service -- it
only calls an official, licensed API endpoint.

Provider is Google's Programmable Search Engine (Custom Search JSON API):
free tier (100 queries/day), and accepts the same query operators
(site:, intitle:, filetype:, ...) as typing them into google.com -- so
this is real Google-dork capability, just through the legitimate API
instead of scraping google.com/search (which its ToS explicitly forbids).
Setup:
  1. Create a search engine at https://programmablesearchengine.google.com
     configured to search the entire web (not just specific sites).
  2. Get an API key at https://console.cloud.google.com with the
     "Custom Search API" enabled.

Bing Web Search API is NOT supported -- Microsoft fully retired it on
2025-08-11 (existing keys now return HTTP 410 Gone, no new signups have
been possible since). Configuring provider: bing returns a clear error
explaining that, instead of a confusing API failure.

NOTE: unlike this project's other modules, the Google branch below was
NOT live-verified against a real key (no account available in this
environment) -- field names come from Google's published API docs. Every
read uses .get() with a fallback so a schema surprise degrades gracefully
instead of raising, but treat this as unverified until tested for real.

Configure via:
  sources:
    search_api:
      enabled: true
      provider: google        # only supported provider (bing is retired)
      api_key: "<your Google Cloud API key>"
      cx: "<your Programmable Search Engine ID>"
      results_per_query: 10   # Google's API caps at 10 per request
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

GOOGLE_ENDPOINT = "https://www.googleapis.com/customsearch/v1"


class SearchAPIPlugin(SourcePlugin):
    name: ClassVar[str] = "search_api"
    category: ClassVar[str] = "search-engine"
    accepts: ClassVar[set[IdentifierType]] = {
        IdentifierType.USERNAME, IdentifierType.EMAIL, IdentifierType.PHONE, IdentifierType.NAME,
    }
    requires_api_key: ClassVar[bool] = True
    description: ClassVar[str] = "Queries the Google Programmable Search Engine (Custom Search API) for mentions of the identifier."

    def is_configured(self) -> bool:
        # provider: bing is deliberately still "configured" (api_key alone
        # is enough) so it reaches run() and gets the explicit retirement
        # error below, instead of silently skipping like a plugin nobody
        # set up -- that distinction matters for anyone still on an old
        # config who'd otherwise be confused about why it stopped working.
        if self.config.get("provider", "google") == "bing":
            return bool(self.config.get("api_key"))
        return bool(self.config.get("api_key")) and bool(self.config.get("cx"))

    async def run(self, identifier: Identifier) -> list[Finding]:
        provider = self.config.get("provider", "google")
        if provider == "bing":
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url="", category=self.category,
                title="Bing Web Search API was retired by Microsoft on 2025-08-11 -- set "
                      "sources.search_api.provider to 'google' instead",
            )]
        if provider != "google":
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url="", title=f"Unsupported search_api provider: {provider}", category=self.category,
            )]

        query = f'"{identifier.value}"'
        params = {
            "key": self.config["api_key"],
            "cx": self.config["cx"],
            "q": query,
            "num": str(min(int(self.config.get("results_per_query", 10)), 10)),
        }

        resp = await self.http.get(self.name, GOOGLE_ENDPOINT, params=params)

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=GOOGLE_ENDPOINT, title="search API request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=GOOGLE_ENDPOINT, title=f"search API returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        data = resp.json() or {}
        # Google's Custom Search API omits "items" entirely (not an empty
        # list) when a query has zero results.
        items = data.get("items") or []
        findings = []
        for item in items:
            findings.append(Finding(
                source=self.name,
                identifier=identifier,
                status=MatchStatus.UNCERTAIN,  # a keyword hit isn't proof of identity
                source_url=item.get("link", GOOGLE_ENDPOINT),
                title=item.get("title", "Search result"),
                category=self.category,
                metadata={"snippet": item.get("snippet"), "display_link": item.get("displayLink")},
                evidence_path=resp.evidence_path,
            ))
        return findings
