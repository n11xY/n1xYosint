"""Tests for the pure hash-comparison step in avatar_correlation.py -- no
network access needed. Skips entirely if Pillow/imagehash aren't installed
(optional dependency: pip install -e ".[imagehash]"), same as the module
itself silently no-ops when they're missing.
"""
import pytest

pytest.importorskip("PIL")
pytest.importorskip("imagehash")

from PIL import Image, ImageOps  # noqa: E402
import imagehash  # noqa: E402

from osintrecon.core.avatar_correlation import HAMMING_THRESHOLD, _matches_from_hashes  # noqa: E402
from osintrecon.core.models import Identifier, IdentifierType  # noqa: E402


def _split_image(size: int = 32) -> Image.Image:
    """Left half black, right half white -- real internal contrast (unlike
    a solid-color image, which average_hash can't distinguish from any
    other solid color: every pixel equals the mean, so it always hashes
    to the same all-zero-bit result regardless of which color it is)."""
    img = Image.new("L", (size, size), color=255)
    for x in range(size // 2):
        for y in range(size):
            img.putpixel((x, y), 0)
    return img


def test_identical_images_match():
    ident_a = Identifier(value="alice", type=IdentifierType.USERNAME)
    ident_b = Identifier(value="alice@example.com", type=IdentifierType.EMAIL)
    img = _split_image()
    hashes = {
        (ident_a, "https://example.com/a.png"): imagehash.average_hash(img),
        (ident_b, "https://example.com/b.png"): imagehash.average_hash(img.copy()),
    }

    matches = _matches_from_hashes(hashes)

    assert len(matches) == 1
    finding = matches[0]
    assert finding.source == "avatar_correlation"
    assert finding.metadata["hamming_distance"] == 0
    assert finding.discovered_identifiers == [ident_b]


def test_very_different_images_do_not_match():
    ident_a = Identifier(value="alice", type=IdentifierType.USERNAME)
    ident_b = Identifier(value="bob", type=IdentifierType.USERNAME)
    img = _split_image()
    # Inverting flips every pixel to the opposite side of the mean, so a
    # threshold-based average hash flips every bit -- maximum possible
    # Hamming distance, regardless of exact resize/antialiasing details.
    inverted = ImageOps.invert(img)
    hashes = {
        (ident_a, "https://example.com/a.png"): imagehash.average_hash(img),
        (ident_b, "https://example.com/b.png"): imagehash.average_hash(inverted),
    }

    matches = _matches_from_hashes(hashes)

    assert matches == []


def test_same_identifier_is_never_matched_against_itself():
    ident_a = Identifier(value="alice", type=IdentifierType.USERNAME)
    img = _split_image()
    hashes = {
        (ident_a, "https://example.com/a.png"): imagehash.average_hash(img),
        (ident_a, "https://example.com/a-mirror.png"): imagehash.average_hash(img.copy()),
    }

    matches = _matches_from_hashes(hashes)

    assert matches == []


def test_threshold_is_a_reasonable_strict_value():
    # Sanity check on the constant itself: out of a 64-bit hash, this
    # should stay small enough that it's clearly "strict", not "lenient".
    assert 0 < HAMMING_THRESHOLD <= 10
