"""HaveIBeenPwned source module -- queries the legitimate, paid (~$4.39/mo)
HIBP v3 API for breach and paste exposure of an email address. Requires an
API key (config: sources.hibp.api_key or env OSINTRECON_HIBP_API_KEY), per
HIBP's terms of service. This module never attempts to access breach data
through any unofficial or unauthorized channel.

For a free alternative with no subscription, see xposedornot.py.
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

BREACH_URL = "https://haveibeenpwned.com/api/v3/breachedaccount/{}?truncateResponse=false"
PASTE_URL = "https://haveibeenpwned.com/api/v3/pasteaccount/{}"


class HIBPPlugin(SourcePlugin):
    name: ClassVar[str] = "hibp"
    category: ClassVar[str] = "breach"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.EMAIL}
    requires_api_key: ClassVar[bool] = True
    description: ClassVar[str] = "Checks known data breach + paste exposure via the official HaveIBeenPwned API."

    async def run(self, identifier: Identifier) -> list[Finding]:
        headers = {"hibp-api-key": self.config["api_key"]}
        breaches = await self._fetch_breaches(identifier, headers)
        pastes = await self._fetch_pastes(identifier, headers)
        return breaches + pastes

    async def _fetch_breaches(self, identifier: Identifier, headers: dict) -> list[Finding]:
        url = BREACH_URL.format(identifier.value)
        resp = await self.http.get(self.name, url, headers=headers, expected_statuses={404, 429})

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title="HIBP breach request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status == 404:
            return []
        if resp.status == 429:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title="HIBP rate limit exceeded", category=self.category,
                metadata={"http_status": 429},
            )]
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title=f"HIBP breach endpoint returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        findings = []
        for breach in resp.json() or []:
            findings.append(Finding(
                source=self.name,
                identifier=identifier,
                status=MatchStatus.CONFIRMED,
                source_url=url,
                title=f"Breach exposure: {breach.get('Name', 'unknown')}",
                category=self.category,
                metadata={
                    "breach_name": breach.get("Name"),
                    "domain": breach.get("Domain"),
                    "breach_date": breach.get("BreachDate"),
                    "data_classes": breach.get("DataClasses"),
                    "is_verified": breach.get("IsVerified"),
                    "is_sensitive": breach.get("IsSensitive"),
                },
                evidence_path=resp.evidence_path,
            ))
        return findings

    async def _fetch_pastes(self, identifier: Identifier, headers: dict) -> list[Finding]:
        url = PASTE_URL.format(identifier.value)
        resp = await self.http.get(self.name, url, headers=headers, expected_statuses={404, 429})

        # Errors here are reported but don't duplicate the breach endpoint's
        # error handling verbosity -- a paste-lookup failure is secondary.
        if resp.error is not None or resp.status not in (200, 404):
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title="HIBP paste request failed", category="paste",
                metadata={"error": resp.error, "http_status": resp.status},
            )]
        if resp.status == 404:
            return []

        findings = []
        for paste in resp.json() or []:
            paste_id = paste.get("Id")
            source_link = paste.get("Source", "")
            findings.append(Finding(
                source=self.name,
                identifier=identifier,
                status=MatchStatus.CONFIRMED,
                source_url=url,
                title=f"Paste exposure on {source_link or 'unknown source'}",
                category="paste",
                metadata={
                    "paste_id": paste_id,
                    "paste_source": source_link,
                    "paste_title": paste.get("Title"),
                    "paste_date": paste.get("Date"),
                    "email_count": paste.get("EmailCount"),
                },
                evidence_path=resp.evidence_path,
            ))
        return findings
