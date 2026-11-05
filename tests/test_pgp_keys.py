import asyncio
import base64
import hashlib

from osintrecon.core.models import Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.sources.pgp_keys import PgpKeysPlugin, parse_hkp_index, parse_key, split_uid

CREATED = 1668524410  # 2022-11-15T15:00:10Z


def _key_body() -> bytes:
    # v4 RSA public key: version, creation time, algorithm, MPI n, MPI e.
    n = b"\xc3" + b"\x01" * 127
    e = b"\x01\x00\x01"
    return (bytes([4]) + CREATED.to_bytes(4, "big") + bytes([1])
            + (1024).to_bytes(2, "big") + n + (17).to_bytes(2, "big") + e)


def _armored(*uids: str) -> str:
    body = _key_body()
    data = bytes([0x99]) + len(body).to_bytes(2, "big") + body  # old format, tag 6
    for uid in uids:
        raw = uid.encode()
        data += bytes([0xC0 | 13, len(raw)]) + raw  # new format, tag 13
    b64 = base64.b64encode(data).decode()
    lines = [b64[i:i + 64] for i in range(0, len(b64), 64)]
    return "\n".join(["-----BEGIN PGP PUBLIC KEY BLOCK-----", "Comment: test", "", *lines,
                      "=abcd", "-----END PGP PUBLIC KEY BLOCK-----"])


def test_parse_key_reads_fingerprint_creation_time_and_user_ids():
    key = parse_key(_armored("Jane Doe <jane@example.org>", "Jane Doe (work) <jdoe@corp.example>"))

    body = _key_body()
    expected = hashlib.sha1(b"\x99" + len(body).to_bytes(2, "big") + body).hexdigest().upper()
    assert key["fingerprint"] == expected
    assert key["created_at"] == "2022-11-15T15:00:10Z"
    assert key["user_ids"] == ["Jane Doe <jane@example.org>", "Jane Doe (work) <jdoe@corp.example>"]


def test_parse_key_rejects_text_that_is_not_a_key():
    assert parse_key("No key found for email address x@example.org") is None
    assert parse_key("-----BEGIN PGP PUBLIC KEY BLOCK-----\n\n!!!!\n-----END PGP PUBLIC KEY BLOCK-----") is None


def test_split_uid():
    assert split_uid("Jane Doe (work) <Jane@Example.org>") == ("Jane Doe", "jane@example.org")
    assert split_uid("<jane@example.org>") == (None, "jane@example.org")
    assert split_uid("jane@example.org") == (None, "jane@example.org")


def test_parse_hkp_index_with_and_without_uid_lines():
    keys = parse_hkp_index(
        "info:1:2\n"
        "pub:AAAA:1:2048:1576954850::\n"
        "uid:Jane%20Doe%20<jane@example.org>:1576954850::\n"
        "pub:bbbb:1:2048:::\n"
    )
    assert keys[0] == {"fingerprint": "AAAA", "created_at": "2019-12-21T19:00:50Z",
                       "user_ids": ["Jane Doe <jane@example.org>"]}
    assert keys[1] == {"fingerprint": "BBBB", "created_at": None, "user_ids": []}


class Resp:
    def __init__(self, status=200, text="", error=None):
        self.status, self.text, self.error = status, text, error
        self.evidence_path = None


class Http:
    def __init__(self, vks, hkp):
        self.vks, self.hkp = vks, hkp
        self.calls = []

    async def get(self, source, url, params=None, expected_statuses=None, max_bytes=None):
        self.calls.append((url, params))
        return self.vks if "keys.openpgp.org" in url else self.hkp


def _run(http, email="jane@example.org", config=None):
    plugin = PgpKeysPlugin(config=config or {}, http=http)
    return asyncio.run(plugin.run(Identifier(value=email, type=IdentifierType.EMAIL)))


def test_verified_key_is_confirmed_and_feeds_other_addresses_and_name_back():
    http = Http(Resp(200, _armored("Jane Doe <jane@example.org>", "Jane Doe <jd@other.example>")), Resp(404))

    findings = _run(http, "Jane@Example.org")

    assert len(findings) == 1
    f = findings[0]
    assert f.status == MatchStatus.CONFIRMED
    assert f.metadata["name"] == "Jane Doe"
    assert f.metadata["other_emails"] == ["jd@other.example"]
    assert f.source_url == "https://keys.openpgp.org/search?q=jane%40example.org"
    assert {(i.type, i.value) for i in f.discovered_identifiers} == {
        (IdentifierType.EMAIL, "jd@other.example"), (IdentifierType.NAME, "Jane Doe"),
    }
    assert http.calls[0][0] == "https://keys.openpgp.org/vks/v1/by-email/jane%40example.org"


def test_no_key_anywhere_returns_nothing():
    assert _run(Http(Resp(404, "No key found"), Resp(404, "Not Found"))) == []


def test_unverified_keys_are_uncertain_and_never_feed_enrichment():
    hkp = Resp(200, "info:1:2\npub:AAAA:1:2048:1576954850::\npub:BBBB:1:2048:1276954850::\n"
                    "pub:CCCC:1:2048:1576954850::\nuid:Someone%20<other@example.org>:::\n")

    findings = _run(Http(Resp(404), hkp))

    assert len(findings) == 1
    f = findings[0]
    assert f.status == MatchStatus.UNCERTAIN
    assert f.metadata["key_count"] == 2  # CCCC names a different address
    assert f.metadata["oldest_key_at"] == "2010-06-19T13:40:50Z"
    assert f.discovered_identifiers == []


def test_unverified_keyserver_can_be_switched_off():
    http = Http(Resp(404), Resp(200, "pub:AAAA:1:2048:1576954850::\n"))

    assert _run(http, config={"unverified_keyserver": False}) == []
    assert len(http.calls) == 1


def test_verified_keyserver_outage_is_an_error_row():
    findings = _run(Http(Resp(0, error="TimeoutError"), Resp(404)))

    assert [f.status for f in findings] == [MatchStatus.ERROR]
