"""OpenPGP keyserver source module -- is a public encryption key published
for this email address, and whose name is on it?

Two keyservers, deliberately reported at different confidence levels,
because they make very different promises:

* keys.openpgp.org (Hagrid) only publishes an identity after the address
  owner clicks a confirmation link sent to that address -- an unverified
  user ID is stripped before the key is served. A key found here was
  claimed by whoever controls the mailbox: CONFIRMED. The other verified
  addresses on the same key are fed back as discovered emails, and the
  name in the user ID ("Jane Doe <jane@...>") as a discovered name, the
  same way gravatar.py feeds its display name.
* keyserver.ubuntu.com (the old SKS-style network) accepts any key
  claiming any address, unchecked -- famous addresses have dozens of
  forged keys on it. Useful history (keys from years ago, other names the
  address was published under) but never proof: one UNCERTAIN summary,
  and nothing from it is fed back into enrichment.

Both are free, keyless and documented for exactly this lookup (the VKS
API, and the HKP "op=index&options=mr" machine-readable index).

Live-verified (2026-11-05): keys.openpgp.org returns 200 with an
ASCII-armored key for security@torproject.org and 404 ("No key found for
email address ...") for a random address; keyserver.ubuntu.com returns a
"pub:/uid:" index for a known address and 404 "Not Found" for a random one.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
from datetime import datetime, timezone
from typing import ClassVar, Optional
from urllib.parse import quote, unquote

from osintrecon.core import normalize
from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

VKS_URL = "https://keys.openpgp.org/vks/v1/by-email/{}"
VKS_PAGE = "https://keys.openpgp.org/search?q={}"
HKP_URL = "https://keyserver.ubuntu.com/pks/lookup"
HKP_PAGE = "https://keyserver.ubuntu.com/pks/lookup?search={}&op=index"

# An armored key with many signatures can be large; a person's key is far below this.
MAX_KEY_BYTES = 512 * 1024
MAX_HKP_BYTES = 256 * 1024
MAX_UNVERIFIED_KEYS = 5

_UID_RE = re.compile(r"^\s*(?P<name>[^<]*?)\s*(?:\([^)]*\)\s*)?<(?P<email>[^>]+)>\s*$")


def _dearmor(text: str) -> Optional[bytes]:
    """The binary packets inside an ASCII-armored key block, or None."""
    lines = text.strip().splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.startswith("-----BEGIN PGP PUBLIC KEY BLOCK"))
    except StopIteration:
        return None
    body: list[str] = []
    in_headers = True
    for line in lines[start + 1:]:
        line = line.strip()
        if line.startswith("-----END"):
            break
        if in_headers:
            if not line:
                in_headers = False
            elif ":" not in line:  # no armor headers at all: this is already data
                in_headers = False
                body.append(line)
            continue
        if line.startswith("="):  # CRC24 checksum line
            break
        body.append(line)
    try:
        return base64.b64decode("".join(body), validate=False)
    except (binascii.Error, ValueError):
        return None


def _packets(data: bytes):
    """(tag, body) for each OpenPGP packet (RFC 9580 section 4.2)."""
    i = 0
    while i < len(data):
        header = data[i]
        if not header & 0x80:
            return
        if header & 0x40:  # new format
            tag = header & 0x3F
            i += 1
            if i >= len(data):
                return
            first = data[i]
            if first < 192:
                length, i = first, i + 1
            elif first < 224:
                if i + 1 >= len(data):
                    return
                length, i = ((first - 192) << 8) + data[i + 1] + 192, i + 2
            elif first == 255:
                length, i = int.from_bytes(data[i + 1:i + 5], "big"), i + 5
            else:  # partial body lengths: never used for keys or user IDs
                return
        else:  # old format
            tag = (header >> 2) & 0x0F
            kind = header & 0x03
            i += 1
            if kind == 3:
                return
            size = (1, 2, 4)[kind]
            length, i = int.from_bytes(data[i:i + size], "big"), i + size
        yield tag, data[i:i + length]
        i += length


def parse_key(armored: str) -> Optional[dict]:
    """Fingerprint, creation time and user IDs of the primary key, or None
    if the text isn't a readable key."""
    data = _dearmor(armored)
    if not data:
        return None
    fingerprint = created = None
    user_ids: list[str] = []
    for tag, body in _packets(data):
        if tag == 6 and fingerprint is None and len(body) >= 5:  # primary public key
            version = body[0]
            created = int.from_bytes(body[1:5], "big")
            if version == 4:
                fingerprint = hashlib.sha1(b"\x99" + len(body).to_bytes(2, "big") + body).hexdigest()
            elif version == 6:
                fingerprint = hashlib.sha256(b"\x9b" + len(body).to_bytes(4, "big") + body).hexdigest()
        elif tag == 13:  # user ID
            uid = body.decode("utf-8", errors="replace").strip()
            if uid and uid not in user_ids:
                user_ids.append(uid)
    if fingerprint is None:
        return None
    return {
        "fingerprint": fingerprint.upper(),
        "created_at": _iso(created),
        "user_ids": user_ids,
    }


def split_uid(uid: str) -> tuple[Optional[str], Optional[str]]:
    """("Jane Doe", "jane@example.org") from "Jane Doe (work) <jane@example.org>"."""
    m = _UID_RE.match(uid)
    if not m:
        return None, (uid.strip().lower() if "@" in uid and " " not in uid.strip() else None)
    return (m.group("name") or None), m.group("email").strip().lower()


def parse_hkp_index(text: str) -> list[dict]:
    """Keys from a machine-readable HKP index: pub/uid lines."""
    keys: list[dict] = []
    for line in text.splitlines():
        fields = line.split(":")
        if fields[0] == "pub" and len(fields) > 1:
            created = fields[4] if len(fields) > 4 else ""
            keys.append({
                "fingerprint": fields[1].upper(),
                "created_at": _iso(int(created)) if created.isdigit() else None,
                "user_ids": [],
            })
        elif fields[0] == "uid" and keys and len(fields) > 1:
            keys[-1]["user_ids"].append(unquote(fields[1]))
    return keys


def _iso(timestamp: Optional[int]) -> Optional[str]:
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PgpKeysPlugin(SourcePlugin):
    name: ClassVar[str] = "pgp_keys"
    category: ClassVar[str] = "profile-directory"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.EMAIL}
    description: ClassVar[str] = (
        "Looks the address up on OpenPGP keyservers: keys.openpgp.org (owner-verified, CONFIRMED) "
        "and keyserver.ubuntu.com (anyone can upload, UNCERTAIN)."
    )

    async def run(self, identifier: Identifier) -> list[Finding]:
        email = identifier.value.strip().lower()
        findings: list[Finding] = []
        verified = await self._verified_key(identifier, email)
        if verified:
            findings.append(verified)
        if self.config.get("unverified_keyserver", True):
            unverified = await self._unverified_keys(identifier, email)
            if unverified:
                findings.append(unverified)
        return findings

    async def _verified_key(self, identifier: Identifier, email: str) -> Optional[Finding]:
        url = VKS_URL.format(quote(email, safe=""))
        resp = await self.http.get(self.name, url, expected_statuses={404}, max_bytes=MAX_KEY_BYTES)
        if resp.error is not None:
            return Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR, source_url=url,
                title="keys.openpgp.org request failed", category=self.category, metadata={"error": resp.error},
            )
        if resp.status == 404:
            return None
        if resp.status != 200:
            return Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR, source_url=url,
                title=f"keys.openpgp.org returned {resp.status}", category=self.category,
                metadata={"http_status": resp.status},
            )
        key = parse_key(resp.text)
        if key is None:
            return None

        names: list[str] = []
        emails: list[str] = []
        for uid in key["user_ids"]:
            uid_name, uid_email = split_uid(uid)
            if uid_name and uid_name not in names:
                names.append(uid_name)
            if uid_email and uid_email not in emails:
                emails.append(uid_email)

        discovered: list[Identifier] = [
            Identifier(value=other, type=IdentifierType.EMAIL)
            for other in emails if other != email and normalize.EMAIL_RE.match(other)
        ]
        for uid_name in names:
            if normalize.NAME_RE.match(uid_name):
                discovered.append(Identifier(value=normalize.normalize_name(uid_name), type=IdentifierType.NAME))

        return Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.CONFIRMED,
            source_url=VKS_PAGE.format(quote(email, safe="")),
            title=f"Verified OpenPGP key for '{email}'" + (f": {names[0]}" if names else ""),
            category=self.category,
            metadata={
                "keyserver": "keys.openpgp.org",
                "name": names[0] if names else None,
                "names": names,
                "fingerprint": key["fingerprint"],
                "created_at": key["created_at"],
                "user_ids": key["user_ids"],
                "other_emails": [e for e in emails if e != email],
                "verified": True,
                "api_url": url,
            },
            discovered_identifiers=discovered,
            evidence_path=resp.evidence_path,
        )

    async def _unverified_keys(self, identifier: Identifier, email: str) -> Optional[Finding]:
        params = {"op": "index", "options": "mr", "fingerprint": "on", "exact": "on", "search": email}
        resp = await self.http.get(self.name, HKP_URL, params=params, expected_statuses={404},
                                   max_bytes=MAX_HKP_BYTES)
        if resp.error is not None or resp.status != 200:
            return None  # the unverified server is a bonus; its outages aren't worth an error row
        # The server matched the exact address already; current Hockeypuck
        # omits uid lines from the index, so a key without any is taken on
        # that match, and one whose uids are listed must name the address.
        keys = [k for k in parse_hkp_index(resp.text)
                if not k["user_ids"] or any(split_uid(uid)[1] == email for uid in k["user_ids"])]
        if not keys:
            return None
        keys.sort(key=lambda k: k["created_at"] or "")
        names = sorted({n for k in keys for n, e in map(split_uid, k["user_ids"]) if n and e == email})
        return Finding(
            source=self.name,
            identifier=identifier,
            status=MatchStatus.UNCERTAIN,
            source_url=HKP_PAGE.format(quote(email, safe="")),
            title=(f"{len(keys)} unverified OpenPGP key(s) on keyserver.ubuntu.com claim '{email}' "
                   "(anyone can upload any address -- not proof of ownership)"),
            category=self.category,
            metadata={
                "keyserver": "keyserver.ubuntu.com",
                "verified": False,
                "key_count": len(keys),
                "names_claimed": names[:10],
                "oldest_key_at": keys[0]["created_at"],
                "keys": keys[:MAX_UNVERIFIED_KEYS],
            },
        )
