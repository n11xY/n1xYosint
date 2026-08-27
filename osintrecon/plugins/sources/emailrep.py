"""EmailRep.io source module -- queries the official EmailRep.io public API
for email reputation signal (known breach exposure, suspicious/malicious
activity flags, domain existence, first/last-seen dates). Works
unauthenticated at a lower rate limit; an optional API key
(https://emailrep.io/key) raises the limit.

Config:
  sources:
    emailrep:
      enabled: true
      api_key: null   # optional
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://emailrep.io/{}"


class EmailRepPlugin(SourcePlugin):
    name: ClassVar[str] = "emailrep"
    category: ClassVar[str] = "breach"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.EMAIL}
    description: ClassVar[str] = "Queries the EmailRep.io API for reputation/exposure signal on an email address."

    async def run(self, identifier: Identifier) -> list[Finding]:
        url = API_URL.format(identifier.value)
        headers = {}
        api_key = self.config.get("api_key")
        if api_key:
            headers["Key"] = api_key

        resp = await self.http.get(self.name, url, headers=headers, expected_statuses={400, 401, 404, 429})

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title="EmailRep request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status == 429:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title="EmailRep rate limit exceeded", category=self.category,
                metadata={"http_status": 429},
            )]
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=url, title=f"EmailRep returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        data = resp.json() or {}
        details = data.get("details", {})
        # Informational reputation signal, not a "does this identity exist"
        # confirmation -- kept at PROBABLE regardless of the reputation value.
        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.PROBABLE,
            source_url=url,
            title=f"Email reputation: {data.get('reputation', 'unknown')}",
            category=self.category,
            metadata={
                "reputation": data.get("reputation"),
                "suspicious": data.get("suspicious"),
                "references": data.get("references"),
                "blacklisted": details.get("blacklisted"),
                "malicious_activity": details.get("malicious_activity"),
                "credentials_leaked": details.get("credentials_leaked"),
                "data_breach": details.get("data_breach"),
                "domain_exists": details.get("domain_exists"),
                "domain_reputation": details.get("domain_reputation"),
                "first_seen": details.get("first_seen"),
                "last_seen": details.get("last_seen"),
            },
            evidence_path=resp.evidence_path,
        )]
