"""Paste-site exposure search via psbdmp.ws's public search API, which indexes
already-public Pastebin dumps for keyword search. No authentication is
required; this only surfaces content that psbdmp has already made publicly
searchable, and never accesses Pastebin content behind any access control.
"""
from __future__ import annotations

from typing import ClassVar
from urllib.parse import quote

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

SEARCH_URL = "https://psbdmp.ws/api/search/v3/{}"


class PastebinSearchPlugin(SourcePlugin):
    name: ClassVar[str] = "pastebin_search"
    category: ClassVar[str] = "paste"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.USERNAME, IdentifierType.EMAIL}
    description: ClassVar[str] = "Searches public paste dumps (via psbdmp.ws) for keyword mentions."

    async def run(self, identifier: Identifier) -> list[Finding]:
        url = SEARCH_URL.format(quote(identifier.value, safe=""))
        resp = await self.http.get(self.name, url, expected_statuses={404})

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title="psbdmp request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status != 200:
            return []

        data = resp.json() or {}
        # psbdmp.ws v3 responds with {"count": N, "data": [{"id": ..., "date": ...}, ...]}
        matches = data.get("data") or []
        findings = []
        for item in matches[:25]:
            paste_id = item.get("id")
            paste_url = f"https://pastebin.com/{paste_id}" if paste_id else url
            findings.append(Finding(
                source=self.name,
                identifier=identifier,
                status=MatchStatus.PROBABLE,
                source_url=paste_url,
                title=f"Mention found in paste {paste_id or '(unknown id)'}",
                category=self.category,
                metadata={"paste_date": item.get("date"), "search_url": url},
                evidence_path=resp.evidence_path,
            ))
        return findings
