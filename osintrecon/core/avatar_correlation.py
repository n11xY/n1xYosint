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
from io import BytesIO

from osintrecon.core.http_client import AsyncHttpClient
from osintrecon.core.logging_setup import get_logger
from osintrecon.core.models import Finding, Identifier, MatchStatus

log = get_logger("avatar_correlation")

# Out of a 64-bit average-hash (8x8), how many bits are allowed to differ
# before two images stop counting as a match. Low enough that unrelated
# photos essentially never collide by chance; high enough to survive a
# platform's own re-encoding/resizing of an uploaded avatar.
HAMMING_THRESHOLD = 6


def available() -> bool:
    return importlib.util.find_spec("PIL") is not None and importlib.util.find_spec("imagehash") is not None


async def find_avatar_matches(findings: list[Finding], http: AsyncHttpClient) -> list[Finding]:
    if not available():
        return []

    import imagehash
    from PIL import Image

    # One fetch per distinct (identifier, avatar_url) pair. The same
    # identifier can legitimately carry more than one avatar_url finding
    # (e.g. re-run across enrichment rounds); dedup on the pair, not just
    # the URL, so each identifier still gets its own hash entry to compare.
    candidates: list[tuple[Identifier, str]] = []
    seen: set[tuple[Identifier, str]] = set()
    for f in findings:
        url = (f.metadata or {}).get("avatar_url")
        if not url:
            continue
        key = (f.identifier, url)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(key)

    if len(candidates) < 2:
        return []

    hashes: dict[tuple[Identifier, str], "imagehash.ImageHash"] = {}
    for ident, url in candidates:
        # Never cache binary content through the (text-oriented) response
        # cache -- see HttpResponse.content's docstring.
        resp = await http.get("avatar_hash", url, allow_cache=False)
        if resp.error is not None or resp.status != 200 or not resp.content:
            continue
        try:
            hashes[(ident, url)] = imagehash.average_hash(Image.open(BytesIO(resp.content)))
        except Exception as exc:  # noqa: BLE001 -- not every URL is a decodable image
            log.debug("avatar hash failed for %s: %s", url, exc)
            continue

    return _matches_from_hashes(hashes)


def _matches_from_hashes(hashes: "dict[tuple[Identifier, str], imagehash.ImageHash]") -> list[Finding]:
    """Pure pairwise-comparison step, split out from find_avatar_matches so
    it's testable without a network fetch -- feed it real ImageHash objects
    (computed from in-memory images) and it needs no HTTP client at all."""
    matches: list[Finding] = []
    items = list(hashes.items())
    for i in range(len(items)):
        (ident_a, url_a), hash_a = items[i]
        for j in range(i + 1, len(items)):
            (ident_b, url_b), hash_b = items[j]
            if ident_a == ident_b:
                continue
            distance = hash_a - hash_b
            if distance > HAMMING_THRESHOLD:
                continue
            matches.append(Finding(
                source="avatar_correlation",
                identifier=ident_a,
                status=MatchStatus.PROBABLE,
                source_url=url_a,
                title=f"Profile photo closely matches {ident_b.type.value} '{ident_b.value}'",
                category="correlation",
                metadata={
                    "matched_identifier": ident_b.value,
                    "matched_identifier_type": ident_b.type.value,
                    "avatar_url_a": url_a,
                    "avatar_url_b": url_b,
                    "hamming_distance": distance,
                },
                discovered_identifiers=[ident_b],
            ))
    return matches
