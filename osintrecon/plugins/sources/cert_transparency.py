"""Certificate transparency for a domain: every public TLS certificate is
logged, so the logs list the host names a domain has had certificates for
(mail., vpn., staging., forgotten projects ...) and when the first one was
issued.

Two free, keyless sources:

* crt.sh -- the full history, expired certificates included, so it gives
  the earliest certificate date. Known to answer 502 when overloaded.
* Cert Spotter (SSLMate) -- currently valid certificates only, used when
  crt.sh fails. Its anonymous limit is small (10 requests an hour), which
  a fallback stays well inside.

Only names under the searched domain are kept, wildcards folded into
their base name. A domain with no logged certificate returns nothing.

Live-verified (2026-11-06): Cert Spotter answered torproject.org with its
current certificates while crt.sh returned 502 for the same query.
"""
from __future__ import annotations

from typing import ClassVar, Optional
from urllib.parse import quote

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

CRTSH_URL = "https://crt.sh/?q={}&output=json"
CERTSPOTTER_URL = "https://api.certspotter.com/v1/issuances"
PAGE_URL = "https://crt.sh/?q={}"
MAX_NAMES = 100
MAX_BODY = 6 * 1024 * 1024


def _names_under(domain: str, raw_names) -> set[str]:
    names = set()
    for raw in raw_names:
        for name in str(raw).lower().split():
            name = name.strip().lstrip("*.").rstrip(".")
            if name == domain or name.endswith("." + domain):
                names.add(name)
    return names


class CertTransparencyPlugin(SourcePlugin):
    name: ClassVar[str] = "cert_transparency"
    category: ClassVar[str] = "domain"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.DOMAIN}
    description: ClassVar[str] = (
        "Host names a domain has had TLS certificates for, from certificate transparency logs "
        "(crt.sh, falling back to Cert Spotter)."
    )

    async def run(self, identifier: Identifier) -> list[Finding]:
        domain = identifier.value
        summary = await self._crtsh(domain)
        source = "crt.sh"
        if summary is None:
            summary = await self._certspotter(domain)
            source = "Cert Spotter"
        if summary is None:
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url=PAGE_URL.format(quote("%." + domain, safe="")),
                title="Certificate transparency logs unavailable (crt.sh and Cert Spotter)",
                category=self.category, metadata={"error": "both sources failed"},
            )]
        names, issuers, first_seen, last_seen = summary
        if not names:
            return []
        shown = sorted(names, key=lambda n: (n.count("."), n))[:MAX_NAMES]
        return [Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=PAGE_URL.format(quote("%." + domain, safe="")),
            title=f"{len(names)} host name(s) in certificate transparency logs for {domain}",
            category=self.category,
            metadata={
                "log_source": source,
                "name_count": len(names),
                "names": shown,
                "subdomains": [n for n in shown if n != domain],
                "issuers": sorted(issuers)[:10],
                "first_certificate_at": first_seen,
                "latest_certificate_at": last_seen,
            },
        )]

    async def _crtsh(self, domain: str) -> Optional[tuple]:
        url = CRTSH_URL.format(quote("%." + domain, safe=""))
        resp = await self.http.get(self.name, url, max_bytes=MAX_BODY)
        if resp.error is not None or resp.status != 200:
            return None
        rows = resp.json()
        if not isinstance(rows, list):
            return None
        names = _names_under(domain, (r.get("name_value", "") for r in rows if isinstance(r, dict)))
        names |= _names_under(domain, (r.get("common_name", "") for r in rows if isinstance(r, dict)))
        issuers = {self._issuer_org(r.get("issuer_name", "")) for r in rows if isinstance(r, dict)} - {""}
        dates = sorted(str(r.get("not_before")) for r in rows if isinstance(r, dict) and r.get("not_before"))
        return names, issuers, (dates[0] if dates else None), (dates[-1] if dates else None)

    async def _certspotter(self, domain: str) -> Optional[tuple]:
        # "expand" repeats, which a params dict can't express.
        url = (f"{CERTSPOTTER_URL}?domain={quote(domain, safe='')}"
               "&include_subdomains=true&expand=dns_names&expand=issuer")
        resp = await self.http.get(self.name, url, max_bytes=MAX_BODY)
        if resp.error is not None or resp.status != 200:
            return None
        rows = resp.json()
        if not isinstance(rows, list):
            return None
        names = _names_under(domain, (n for r in rows if isinstance(r, dict) for n in r.get("dns_names") or []))
        issuers = {((r.get("issuer") or {}).get("friendly_name") or "") for r in rows if isinstance(r, dict)} - {""}
        dates = sorted(str(r.get("not_before")) for r in rows if isinstance(r, dict) and r.get("not_before"))
        return names, issuers, (dates[0] if dates else None), (dates[-1] if dates else None)

    @staticmethod
    def _issuer_org(issuer_name: str) -> str:
        for part in str(issuer_name).split(","):
            key, _, value = part.strip().partition("=")
            if key == "O":
                return value.strip().strip('"')
        return ""
