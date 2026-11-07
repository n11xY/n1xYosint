"""Cross-references profile-photo URLs collected by other source modules
(currently github.py and gravatar.py, both already populate
`metadata["avatar_url"]`) to spot the same real person reusing an
identical or near-identical avatar across accounts that share no
identifier at all -- a signal correlation.py's exact-identifier union-find
structurally can't see.

Optional: needs Pillow + imagehash (`pip install -e ".[imagehash]"`). If
either isn't installed, this step is silently skipped, exactly like any
other optional/unconfigured source -- no separate enable/disable flag
needed for that case.

Despite being a fuzzy-match technique, this is a deliberate, narrow
exception to correlation.py's "never fuzzy-match" rule -- see that
module's docstring. The two aren't comparable: common names collide by
coincidence constantly ("John Smith"), but two *different* real people
having a near-pixel-identical photo essentially never happens by chance.
Kept strict (a small Hamming-distance threshold) and always reports as
PROBABLE, never CONFIRMED -- a strong hint for a human reviewer to look
at, not an automatic identity merge they can't inspect or undo.
"""
from __future__ import annotations

import importlib.util
import re
from io import BytesIO
from typing import TYPE_CHECKING

from osintrecon.core.http_client import AsyncHttpClient
from osintrecon.core.logging_setup import get_logger
from osintrecon.core.models import Finding, Identifier, MatchStatus

if TYPE_CHECKING:  # optional dependency: imported for real only inside find_avatar_matches
    import imagehash

log = get_logger("avatar_correlation")

# Out of a 64-bit average-hash (8x8), how many bits are allowed to differ
# before two images stop counting as a match. Low enough that unrelated
# photos essentially never collide by chance; high enough to survive a
# platform's own re-encoding/resizing of an uploaded avatar.
HAMMING_THRESHOLD = 6


def available() -> bool:
    return importlib.util.find_spec("PIL") is not None and importlib.util.find_spec("imagehash") is not None


# Profile pictures are small; these bound what a hostile "avatar" URL can
# cost: the bytes downloaded, and the canvas Pillow is allowed to decode.
AVATAR_MAX_BYTES = 2 * 1024 * 1024
AVATAR_MAX_PIXELS = 4096 * 4096

# Platform placeholder images ("missing.png", "default_avatar.png", ...):
# everyone without a photo shares them, so they say nothing about who an
# account belongs to.
_PLACEHOLDER = re.compile(r"(missing|default|placeholder|blank|anonymous|no[-_]?avatar)[^/]*\.(png|jpe?g|gif|svg|webp)",
                          re.IGNORECASE)


# Bounds on one run's photo comparison: profile photos are now read from
# every found account's page, not only the two avatar plugins.
MAX_AVATARS = 60
FETCH_CONCURRENCY = 8
_GRAVATAR_URL = re.compile(r"(?:^|//)(?:[a-z0-9]+\.)?gravatar\.com/avatar/", re.IGNORECASE)


async def find_avatar_matches(findings: list[Finding], http: AsyncHttpClient) -> list[Finding]:
    if not available():
        return []

    import asyncio

    import imagehash
    from PIL import Image

    # One fetch per distinct (identifier, avatar_url) pair, from found
    # accounts only. Gravatar photos are compared by their hash instead
    # (account_evidence.py): the same hash is the same email, and different
    # hashes can still share Gravatar's default silhouette.
    candidates: list[tuple[Identifier, str]] = []
    sources: dict[tuple[Identifier, str], str] = {}
    seen: set[tuple[Identifier, str]] = set()
    for f in findings:
        if f.status not in (MatchStatus.CONFIRMED, MatchStatus.PROBABLE):
            continue
        url = (f.metadata or {}).get("avatar_url")
        if not isinstance(url, str) or not url or _PLACEHOLDER.search(url) or _GRAVATAR_URL.search(url):
            continue
        key = (f.identifier, url)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(key)
        sources[key] = f.source
        if len(candidates) >= MAX_AVATARS:
            break

    if len(candidates) < 2:
        return []

    gate = asyncio.Semaphore(FETCH_CONCURRENCY)

    async def fetch(ident: Identifier, url: str):
        async with gate:
            # Never cache binary content through the (text-oriented) response
            # cache -- see HttpResponse.content's docstring.
            resp = await http.get("avatar_hash", url, allow_cache=False, max_bytes=AVATAR_MAX_BYTES)
        if resp.error is not None or resp.status != 200 or not resp.content:
            return None
        try:
            with Image.open(BytesIO(resp.content)) as img:
                # The header's dimensions, checked before decoding a pixel: a
                # tiny file can declare a huge canvas (a decompression bomb).
                if img.width * img.height > AVATAR_MAX_PIXELS:
                    log.debug("avatar at %s declares %dx%d, skipped", url, img.width, img.height)
                    return None
                return imagehash.average_hash(img)
        except Exception as exc:  # noqa: BLE001 -- not every URL is a decodable image
            log.debug("avatar hash failed for %s: %s", url, exc)
            return None

    results = await asyncio.gather(*(fetch(i, u) for i, u in candidates), return_exceptions=True)
    hashes = {key: h for key, h in zip(candidates, results) if h is not None and not isinstance(h, BaseException)}
    return _matches_from_hashes(hashes, sources)


def _matches_from_hashes(hashes: "dict[tuple[Identifier, str], imagehash.ImageHash]",
                         sources: "dict[tuple[Identifier, str], str] | None" = None) -> list[Finding]:
    """Pure pairwise-comparison step, split out from find_avatar_matches so
    it's testable without a network fetch -- feed it real ImageHash objects
    (computed from in-memory images) and it needs no HTTP client at all.

    Two different accounts on the *same* platform with the same picture
    are almost always both showing that platform's default avatar (seen
    live: Scratch serves its default under a per-user URL), so a match is
    only counted across different platforms. The same identifier on two
    platforms is compared (that's the username search's own question:
    is the "alice" here the "alice" there?); without known sources it isn't."""
    from osintrecon.core.timeline import platform_key

    sources = sources or {}
    matches: list[Finding] = []
    items = list(hashes.items())
    for i in range(len(items)):
        (ident_a, url_a), hash_a = items[i]
        for j in range(i + 1, len(items)):
            (ident_b, url_b), hash_b = items[j]
            source_a, source_b = sources.get((ident_a, url_a)), sources.get((ident_b, url_b))
            if ident_a == ident_b and (source_a is None or source_b is None):
                continue
            if source_a is not None and source_b is not None and platform_key(source_a) == platform_key(source_b):
                continue
            # imagehash's ImageHash.__sub__ returns a numpy integer (e.g.
            # numpy.int64), not a plain int -- json.dumps() (export_json,
            # also run on every web search now via results_store.py) can't
            # serialize that, so it's cast here rather than wherever it's
            # eventually written out.
            distance = int(hash_a - hash_b)
            if distance > HAMMING_THRESHOLD:
                continue
            metadata = {
                "matched_identifier": ident_b.value,
                "matched_identifier_type": ident_b.type.value,
                "avatar_url_a": url_a,
                "avatar_url_b": url_b,
                "hamming_distance": distance,
            }
            if source_a is not None and source_b is not None:
                metadata["account_a"] = [platform_key(source_a), ident_a.value.lower()]
                metadata["account_b"] = [platform_key(source_b), ident_b.value.lower()]
            matches.append(Finding(
                source="avatar_correlation",
                identifier=ident_a,
                status=MatchStatus.PROBABLE,
                source_url=url_a,
                title=f"Profile photo closely matches {ident_b.type.value} '{ident_b.value}'"
                      + (f" on {source_b.split(':', 1)[-1]}" if source_b else ""),
                category="correlation",
                metadata=metadata,
                discovered_identifiers=[ident_b] if ident_b != ident_a else [],
            ))
    return matches
