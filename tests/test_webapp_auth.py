import pytest

pytest.importorskip("bcrypt")

from webapp import auth  # noqa: E402


def test_hash_and_verify_roundtrip():
    hashed = auth.hash_password("correct horse battery staple")
    assert auth.verify_password("correct horse battery staple", hashed)
    assert not auth.verify_password("wrong password", hashed)


def test_verify_password_rejects_malformed_hash():
    assert not auth.verify_password("anything", "not-a-real-bcrypt-hash")


def test_validate_username_accepts_reasonable_names():
    assert auth.validate_username("n11xY") is None
    assert auth.validate_username("john.doe-99") is None


def test_validate_username_rejects_too_short_or_invalid_chars():
    assert auth.validate_username("ab") is not None
    assert auth.validate_username("has spaces") is not None
    assert auth.validate_username("<script>") is not None


def test_validate_password_enforces_minimum_length():
    assert auth.validate_password("short") is not None
    assert auth.validate_password("this is long enough") is None
