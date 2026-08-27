"""Hunter.io source module -- uses the official Hunter.io Email Verifier API
to confirm whether an email address is real/deliverable, plus signal on
whether it's a disposable, webmail, or gibberish-looking address. Requires a
free-tier API key from https://hunter.io/api-keys.

Config:
  sources:
    hunter_io:
      enabled: true
      api_key: "<your hunter.io api key>"
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://api.hunter.io/v2/email-verifier"

# Hunter's verification result -> how confident we are the address is real/in-use.
RESULT_STATUS = {
    "deliverable": MatchStatus.CONFIRMED,
    "risky": MatchStatus.PROBABLE,
    "unknown": MatchStatus.UNCERTAIN,
}


class HunterIOPlugin(SourcePlugin):
    name: ClassVar[str] = "hunter_io"
    category: ClassVar[str] = "profile-directory"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.EMAIL}
    requires_api_key: ClassVar[bool] = True
    description: ClassVar[str] = "Verifies email deliverability/validity via the official Hunter.io API."

    async def run(self, identifier: Identifier) -> list[Finding]:
        params = {"email": identifier.value, "api_key": self.config["api_key"]}
        resp = await self.http.get(self.name, API_URL, params=params)

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=API_URL, title="Hunter.io request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=API_URL, title=f"Hunter.io returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        data = (resp.json() or {}).get("data", {})
        result = data.get("result")  # "deliverable" | "undeliverable" | "risky" | "unknown"
        if result == "undeliverable":
            return []  # treated as evidence the address doesn't actually exist

        status = RESULT_STATUS.get(result, MatchStatus.UNCERTAIN)
        return [Finding(
            source=self.name,
            identifier=identifier,
            status=status,
            source_url=API_URL,
            title=f"Email verification result: {result}",
            category=self.category,
            metadata={
                "result": result,
                "score": data.get("score"),
                "disposable": data.get("disposable"),
                "webmail": data.get("webmail"),
                "gibberish": data.get("gibberish"),
                "mx_records": data.get("mx_records"),
                "smtp_check": data.get("smtp_check"),
                "block": data.get("block"),
            },
            evidence_path=resp.evidence_path,
        )]
