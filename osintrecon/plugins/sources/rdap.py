"""Domain registration data over RDAP, the structured, documented successor
to WHOIS: when a domain was registered and expires, its registrar, name
servers and status. rdap.org redirects each query to the registry that
holds the domain, free and keyless.

Most registries redact the registrant's personal details (GDPR); when a
registry does publish a registrant organisation or name it is reported,
and a published registrant email is fed back as a discovered identifier.
Registrar and abuse contacts are the registrar's own, never the
registrant's, and are not.

Live-verified (2026-11-06): torproject.org returns registration
2006-10-17, registrar joker.com and six name servers; a domain that isn't
registered returns 404.
"""
from __future__ import annotations

from typing import ClassVar, Optional
from urllib.parse import quote

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.core.normalize import EMAIL_RE
from osintrecon.plugins.base import SourcePlugin

API_URL = "https://rdap.org/domain/{}"
WHOIS_PAGE = "https://lookup.icann.org/en/lookup?name={}"


def _vcard(entity: dict, field: str) -> Optional[str]:
    card = entity.get("vcardArray")
    if not isinstance(card, list) or len(card) < 2 or not isinstance(card[1], list):
        return None
    for entry in card[1]:
        if isinstance(entry, list) and len(entry) >= 4 and entry[0] == field and isinstance(entry[3], str):
            value = entry[3].strip()
            if value and "redacted" not in value.lower():
                return value
    return None


def _entities(data: dict):
    """Every entity, nested ones included (registrars nest their abuse contact)."""
    stack = list(data.get("entities") or [])
    while stack:
        entity = stack.pop()
        if isinstance(entity, dict):
            yield entity
            stack.extend(entity.get("entities") or [])


class RdapPlugin(SourcePlugin):
    name: ClassVar[str] = "rdap"
    category: ClassVar[str] = "domain"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.DOMAIN}
    description: ClassVar[str] = "Domain registration data (dates, registrar, name servers) over RDAP via rdap.org."

    async def run(self, identifier: Identifier) -> list[Finding]:
        domain = identifier.value
        url = API_URL.format(quote(domain, safe=""))
        resp = await self.http.get(self.name, url, expected_statuses={404})
        if resp.error is not None:
            return [Finding(source=self.name, identifier=identifier, status=MatchStatus.ERROR, source_url=url,
                            title="RDAP lookup failed", category=self.category, metadata={"error": resp.error})]
        if resp.status == 404:
            return []
        if resp.status != 200:
            return [Finding(source=self.name, identifier=identifier, status=MatchStatus.ERROR, source_url=url,
                            title=f"RDAP returned {resp.status}", category=self.category,
                            metadata={"http_status": resp.status})]
        data = resp.json() or {}
        events = {str(e.get("eventAction")): e.get("eventDate") for e in data.get("events") or [] if isinstance(e, dict)}

        registrar = registrant = registrant_email = None
        for entity in _entities(data):
            roles = entity.get("roles") or []
            if "registrar" in roles and registrar is None:
                registrar = _vcard(entity, "fn") or _vcard(entity, "org")
            if "registrant" in roles and registrant is None:
                registrant = _vcard(entity, "org") or _vcard(entity, "fn")
                email = _vcard(entity, "email")
                if email and EMAIL_RE.match(email.lower()):
                    registrant_email = email.lower()

        created = events.get("registration")
        discovered = [Identifier(value=registrant_email, type=IdentifierType.EMAIL)] if registrant_email else []
        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=WHOIS_PAGE.format(quote(domain, safe="")),
            title=f"Domain {domain}: registered {(created or '?')[:10]}" + (f" via {registrar}" if registrar else ""),
            category=self.category,
            metadata={
                "created_at": created,
                "expires_at": events.get("expiration"),
                "updated_at": events.get("last changed"),
                "registrar": registrar,
                "registrant": registrant,
                "registrant_email": registrant_email,
                "nameservers": sorted({str(n.get("ldhName", "")).lower() for n in data.get("nameservers") or []
                                       if isinstance(n, dict) and n.get("ldhName")}),
                "status": data.get("status") or [],
                "rdap_url": str(getattr(resp, "url", "") or url),
            },
            discovered_identifiers=discovered,
            evidence_path=resp.evidence_path,
        )]
