"""AbstractAPI Phone Validation -- a live carrier/line-type/location lookup,
complementing phone_lookup's offline check with data pulled from the actual
telco numbering registries instead of a bundled static database (better
coverage for carrier info outside a handful of well-mapped countries).
Requires a free-tier API key from https://www.abstractapi.com/api/phone-validation-api
(free tier: 250 requests/month, HTTPS on every tier -- unlike some
competitors that gate HTTPS behind a paid plan, which would mean sending
the API key in the clear).

NOTE: unlike this project's other modules, the exact response field names
here are taken from AbstractAPI's published docs, not a live test against
a real key (no account available in this environment) -- every field read
uses .get() with a safe fallback so an unexpected/renamed field degrades
to "unknown" rather than raising, but treat this module as unverified
until you've run it against a real number with your own key.

Config:
  sources:
    abstractapi_phone:
      enabled: true
      api_key: "<your abstractapi.com api key>"
"""
from __future__ import annotations

from typing import ClassVar

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://phonevalidation.abstractapi.com/v1/"


class AbstractAPIPhonePlugin(SourcePlugin):
    name: ClassVar[str] = "abstractapi_phone"
    category: ClassVar[str] = "phone"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.PHONE}
    requires_api_key: ClassVar[bool] = True
    description: ClassVar[str] = "Live carrier/line-type/location lookup via the AbstractAPI Phone Validation API."

    async def run(self, identifier: Identifier) -> list[Finding]:
        params = {"api_key": self.config["api_key"], "phone": identifier.value}
        resp = await self.http.get(self.name, API_URL, params=params)

        if resp.error is not None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=API_URL, title="AbstractAPI request failed", category=self.category,
                metadata={"error": resp.error},
            )]
        if resp.status != 200:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=API_URL, title=f"AbstractAPI returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )]

        data = resp.json() or {}
        if not data.get("valid"):
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.NOT_FOUND,
                source_url=API_URL, title="Not a valid, assignable phone number", category=self.category,
                evidence_path=resp.evidence_path,
            )]

        carrier = data.get("carrier") or None
        line_type = data.get("type") or "unknown type"
        location = data.get("location") or None
        country = (data.get("country") or {}).get("name")

        title = f"{str(line_type).capitalize()} number"
        if carrier:
            title += f" on {carrier}"
        if country:
            title += f" ({country})"

        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=API_URL,
            title=title,
            category=self.category,
            metadata={
                "carrier": carrier,
                "line_type": line_type,
                "location": location,
                "country": country,
                "international_format": (data.get("format") or {}).get("international"),
                "local_format": (data.get("format") or {}).get("local"),
            },
            evidence_path=resp.evidence_path,
        )]
