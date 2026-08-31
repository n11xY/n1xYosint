"""Offline phone number analysis using Google's libphonenumber data (the
`phonenumbers` package) -- no API call, no key, no rate limit. Determines
validity, country/region, line type (mobile/landline/VoIP/...), and carrier
name straight from the number's own structure.

Carrier name comes from a bundled numbering-plan database, not a live
lookup against the carrier -- accurate for the vast majority of numbers,
but can lag behind number portability (a number ported to a new carrier
may still show its original one). Flagged in the finding's metadata.
"""
from __future__ import annotations

from typing import ClassVar

import phonenumbers
from phonenumbers import carrier as phone_carrier
from phonenumbers import geocoder as phone_geocoder

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

TYPE_NAMES = {
    phonenumbers.PhoneNumberType.MOBILE: "mobile",
    phonenumbers.PhoneNumberType.FIXED_LINE: "fixed line",
    phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: "fixed line or mobile",
    phonenumbers.PhoneNumberType.TOLL_FREE: "toll-free",
    phonenumbers.PhoneNumberType.PREMIUM_RATE: "premium rate",
    phonenumbers.PhoneNumberType.SHARED_COST: "shared cost",
    phonenumbers.PhoneNumberType.VOIP: "VoIP",
    phonenumbers.PhoneNumberType.PERSONAL_NUMBER: "personal number",
    phonenumbers.PhoneNumberType.PAGER: "pager",
    phonenumbers.PhoneNumberType.UAN: "UAN",
    phonenumbers.PhoneNumberType.VOICEMAIL: "voicemail",
    phonenumbers.PhoneNumberType.UNKNOWN: "unknown type",
}


class PhoneLookupPlugin(SourcePlugin):
    name: ClassVar[str] = "phone_lookup"
    category: ClassVar[str] = "phone"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.PHONE}
    requires_api_key: ClassVar[bool] = False
    description: ClassVar[str] = "Offline validity/country/carrier/line-type parsing via libphonenumber."

    async def run(self, identifier: Identifier) -> list[Finding]:
        try:
            parsed = phonenumbers.parse(identifier.value, None)
        except phonenumbers.NumberParseException as exc:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url="https://github.com/google/libphonenumber",
                title="Could not parse phone number", category=self.category,
                metadata={"error": str(exc)},
            )]

        if not phonenumbers.is_valid_number(parsed):
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.NOT_FOUND,
                source_url="https://github.com/google/libphonenumber",
                title="Not a valid, assignable phone number", category=self.category,
            )]

        country = phone_geocoder.description_for_number(parsed, "en") or None
        carrier_name = phone_carrier.name_for_number(parsed, "en") or None
        line_type = TYPE_NAMES.get(phonenumbers.number_type(parsed), "unknown type")

        title = f"{line_type.capitalize()} number"
        if country:
            title += f" ({country})"

        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url="https://github.com/google/libphonenumber",
            title=title,
            category=self.category,
            metadata={
                "country": country,
                "carrier": carrier_name,
                "carrier_note": "from a numbering-plan database, not a live lookup -- may lag behind porting",
                "line_type": line_type,
                "e164": phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164),
                "international_format": phonenumbers.format_number(
                    parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL,
                ),
            },
        )]
