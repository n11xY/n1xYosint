from osintrecon.core.models import IdentifierType
from osintrecon.core.normalize import normalize_batch, validate_and_build


def test_valid_email():
    ident, reason = validate_and_build("  John.Doe@Example.com ")
    assert reason is None
    assert ident.type == IdentifierType.EMAIL
    assert ident.value == "john.doe@example.com"


def test_invalid_email():
    ident, reason = validate_and_build("not-an-email@")
    assert ident is None
    assert "invalid email" in reason


def test_valid_username_strips_at():
    ident, reason = validate_and_build("@some_user-99")
    assert reason is None
    assert ident.type == IdentifierType.USERNAME
    assert ident.value == "some_user-99"


def test_invalid_username_rejects_bad_chars():
    ident, reason = validate_and_build("bad user!")
    assert ident is None


def test_batch_dedups_and_reports_rejections():
    result = normalize_batch(["alice", "alice", "a@b.com", "", "bad user!"])
    values = {i.value for i in result.identifiers}
    assert values == {"alice", "a@b.com"}
    assert len(result.rejected) == 2  # "" (empty input) and "bad user!" (invalid chars)
