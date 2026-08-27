"""HaveIBeenPwned source module -- queries the legitimate, paid HIBP v3 API
for breach exposure of an email address. Requires an API key
(config: sources.hibp.api_key or env OSINTRECON_HIBP_API_KEY), per HIBP's
terms of service. This module never attempts to access breach data through
any unofficial or unauthorized channel.
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://haveibeenpwned.com/api/v3/breachedaccount/{}?truncateResponse=false"


class HIBPPlugin(SourcePlugin):
    name: ClassVar[str] = "hibp"
    category: ClassVar[str] = "breach"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.EMAIL}
    requires_api_key: ClassVar[bool] = True
    description: ClassVar[str] = "Checks known data breach exposure via the official HaveIBeenPwned API."

    async def run(self, identifier: Identifier) -> list[Finding]:
        url = API_URL.format(identifier.value)
        headers = {"hibp-api-key": self.config["api_key"]}
        resp = await self.http.get(self.name, url, headers=headers, expected_statuses={404, 429})

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title="HIBP request failed", category=self.category,
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
                source_url=url, title=f"HIBP returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        breaches = resp.json() or []
        findings = []
        for breach in breaches:
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
