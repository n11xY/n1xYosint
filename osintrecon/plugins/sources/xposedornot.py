"""XposedOrNot source module -- queries the free, key-less XposedOrNot API
for known data breach exposure of an email address. Unlike HaveIBeenPwned
(now a paid-only API), this is a genuinely free alternative: no signup, no
key, documented at https://xposedornot.com/api_doc.

Free tier limits (per IP): 2 requests/second, 25/hour, 100/day.
"""
from __future__ import annotations

from typing import ClassVar
from urllib.parse import quote

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://api.xposedornot.com/v1/breach-analytics?email={}"


class XposedOrNotPlugin(SourcePlugin):
    name: ClassVar[str] = "xposedornot"
    category: ClassVar[str] = "breach"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.EMAIL}
    description: ClassVar[str] = "Checks known data breach exposure via the free XposedOrNot API (no key required)."

    async def run(self, identifier: Identifier) -> list[Finding]:
        url = API_URL.format(quote(identifier.value, safe=""))
        resp = await self.http.get(self.name, url, expected_statuses={429})

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title="XposedOrNot request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status == 429:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title="XposedOrNot rate limit exceeded", category=self.category,
                metadata={"http_status": 429},
            )]
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title=f"XposedOrNot returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        data = resp.json() or {}
        # A "not found" result is HTTP 200 with every top-level field null.
        breaches = data.get("ExposedBreaches") or []
        # Defensive: some responses nest the list under a "breaches_details" key.
        if isinstance(breaches, dict):
            breaches = breaches.get("breaches_details") or []

        findings = []
        for breach in breaches:
            if not isinstance(breach, dict):
                continue
            findings.append(Finding(
                source=self.name,
                identifier=identifier,
                status=MatchStatus.CONFIRMED,
                source_url=url,
                title=f"Breach exposure: {breach.get('breach', 'unknown')}",
                category=self.category,
                metadata={
                    "breach_name": breach.get("breach"),
                    "domain": breach.get("domain"),
                    "industry": breach.get("industry"),
                    "xposed_date": breach.get("xposed_date"),
                    "xposed_data": breach.get("xposed_data"),
                    "xposed_records": breach.get("xposed_records"),
                    "password_risk": breach.get("password_risk"),
                    "details": breach.get("details"),
                },
                evidence_path=resp.evidence_path,
            ))
        return findings
