import asyncio

from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.core.normalize import build_domain, is_valid_discovered_identifier, normalize_domains
from osintrecon.core.site_health import summarize
from osintrecon.plugins.sources.cert_transparency import CertTransparencyPlugin
from osintrecon.plugins.sources.rdap import RdapPlugin

DOMAIN = Identifier(value="example.org", type=IdentifierType.DOMAIN)


def test_build_domain_normalises_what_people_paste():
    for raw in ("example.org", "https://www.Example.org/path?q=1", "EXAMPLE.ORG.", "http://example.org:8080",
                "user@example.org"):
        ident, why = build_domain(raw)
        assert why is None and ident.value == "example.org" and ident.type is IdentifierType.DOMAIN
    assert build_domain("münchen.de")[0].value == "xn--mnchen-3ya.de"


def test_build_domain_rejects_what_is_not_a_domain():
    for raw in ("", "localhost", "example", "exa mple.org", "-bad.org", "1.2.3.4", "example.c"):
        ident, why = build_domain(raw)
        assert ident is None and why


def test_normalize_domains_dedupes_and_reports():
    result = normalize_domains(["example.org", "www.example.org", "nope"])
    assert [i.value for i in result.identifiers] == ["example.org"]
    assert result.rejected[0][0] == "nope"
    assert is_valid_discovered_identifier(Identifier(value="example.org", type=IdentifierType.DOMAIN))
    assert not is_valid_discovered_identifier(Identifier(value="ex ample.org", type=IdentifierType.DOMAIN))


class Resp:
    def __init__(self, status=200, data=None, error=None, url=""):
        self.status, self._data, self.error, self.url = status, data, error, url
        self.evidence_path = None

    def json(self):
        return self._data


class Http:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    async def get(self, source, url, **kwargs):
        self.calls.append(url)
        for prefix, resp in self.routes.items():
            if url.startswith(prefix):
                return resp
        raise AssertionError(url)


def test_cert_transparency_keeps_names_under_the_domain_and_falls_back():
    certspotter = Resp(data=[
        {"dns_names": ["*.example.org", "mail.example.org", "example.org", "evil-example.org"],
         "issuer": {"friendly_name": "Let's Encrypt"}, "not_before": "2025-01-01T00:00:00Z"},
        {"dns_names": ["vpn.example.org"], "issuer": {"friendly_name": "GlobalSign"}, "not_before": "2026-02-01T00:00:00Z"},
    ])
    http = Http({"https://crt.sh/": Resp(status=502), "https://api.certspotter.com/": certspotter})

    [f] = asyncio.run(CertTransparencyPlugin({}, http).run(DOMAIN))

    assert f.status is MatchStatus.CONFIRMED and f.metadata["log_source"] == "Cert Spotter"
    assert f.metadata["names"] == ["example.org", "mail.example.org", "vpn.example.org"]
    assert f.metadata["subdomains"] == ["mail.example.org", "vpn.example.org"]
    assert f.metadata["issuers"] == ["GlobalSign", "Let's Encrypt"]
    assert f.metadata["first_certificate_at"] == "2025-01-01T00:00:00Z"
    assert "expand=dns_names&expand=issuer" in http.calls[1]


def test_cert_transparency_prefers_crtsh_history():
    crtsh = Resp(data=[{"name_value": "a.example.org\nb.example.org", "common_name": "example.org",
                        "issuer_name": "C=US, O=Let's Encrypt, CN=R3", "not_before": "2014-05-01T00:00:00"}])
    [f] = asyncio.run(CertTransparencyPlugin({}, Http({"https://crt.sh/": crtsh})).run(DOMAIN))
    assert f.metadata["log_source"] == "crt.sh" and f.metadata["name_count"] == 3
    assert f.metadata["issuers"] == ["Let's Encrypt"]


def test_cert_transparency_both_down_is_an_error_and_no_certs_is_nothing():
    down = Http({"https://crt.sh/": Resp(status=502), "https://api.certspotter.com/": Resp(error="timeout")})
    assert [f.status for f in asyncio.run(CertTransparencyPlugin({}, down).run(DOMAIN))] == [MatchStatus.ERROR]
    empty = Http({"https://crt.sh/": Resp(data=[])})
    assert asyncio.run(CertTransparencyPlugin({}, empty).run(DOMAIN)) == []


RDAP_BODY = {
    "events": [{"eventAction": "registration", "eventDate": "2006-10-17T22:02:50Z"},
               {"eventAction": "expiration", "eventDate": "2027-10-17T22:02:50Z"}],
    "entities": [
        {"roles": ["registrar"], "vcardArray": ["vcard", [["fn", {}, "text", "Example Registrar"]]],
         "entities": [{"roles": ["abuse"], "vcardArray": ["vcard", [["email", {}, "text", "abuse@registrar.example"]]]}]},
        {"roles": ["registrant"], "vcardArray": ["vcard", [["org", {}, "text", "Example Org"],
                                                           ["email", {}, "text", "Owner@Example.org"]]]},
    ],
    "nameservers": [{"ldhName": "NS2.EXAMPLE.ORG"}, {"ldhName": "ns1.example.org"}],
    "status": ["client transfer prohibited"],
}


def test_rdap_reads_registration_and_only_the_registrant_email():
    [f] = asyncio.run(RdapPlugin({}, Http({"https://rdap.org/": Resp(data=RDAP_BODY)})).run(DOMAIN))
    assert f.status is MatchStatus.CONFIRMED
    assert f.metadata["created_at"].startswith("2006-10-17") and f.metadata["registrar"] == "Example Registrar"
    assert f.metadata["registrant"] == "Example Org"
    assert f.metadata["nameservers"] == ["ns1.example.org", "ns2.example.org"]
    assert [(i.type, i.value) for i in f.discovered_identifiers] == [(IdentifierType.EMAIL, "owner@example.org")]


def test_rdap_redacted_registrant_and_unregistered_domain():
    redacted = dict(RDAP_BODY, entities=[{"roles": ["registrant"],
                                          "vcardArray": ["vcard", [["fn", {}, "text", "REDACTED FOR PRIVACY"]]]}])
    [f] = asyncio.run(RdapPlugin({}, Http({"https://rdap.org/": Resp(data=redacted)})).run(DOMAIN))
    assert f.metadata["registrant"] is None and f.discovered_identifiers == []
    assert asyncio.run(RdapPlugin({}, Http({"https://rdap.org/": Resp(status=404)})).run(DOMAIN)) == []


def test_site_health_summary():
    def f(site, status, **meta):
        return Finding(source=f"username_sites:{site}", identifier=Identifier("zq1", IdentifierType.USERNAME),
                       status=status, source_url=f"https://{site}/zq1", metadata=meta, title=f"{site}: x")

    report = summarize(
        ["A", "B", "C", "D", "Off"],
        [f("A", MatchStatus.UNCERTAIN), f("B", MatchStatus.ERROR, outcome="access_restricted", http_status=403)],
        {"Off"}, "zq1",
    )
    assert report["sites"] == 4 and report["healthy"] == 2
    assert [r["site"] for r in report["false_positive"]] == ["A"]
    assert report["unavailable"] == [{"site": "B", "outcome": "access_restricted", "http_status": 403, "detail": "B: x"}]
    assert report["disabled"] == ["Off"]
