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


def test_valid_phone_normalizes_to_e164():
    ident, reason = validate_and_build("+1 (415) 867-5309")
    assert reason is None
    assert ident.type == IdentifierType.PHONE
    assert ident.value == "+14158675309"


def test_phone_without_plus_is_not_misclassified_as_phone():
    # No leading "+" -> genuinely ambiguous (could be a numeric username,
    # a Steam ID, ...), so this must NOT be auto-detected as a phone number.
    ident, reason = validate_and_build("15552345678")
    assert reason is None
    assert ident.type == IdentifierType.USERNAME


def test_invalid_phone_number_rejected():
    ident, reason = validate_and_build("+1 555 123")  # too few digits to be a real NANP number
    assert ident is None
    assert "invalid phone" in reason


def test_phone_dedups_across_formatting():
    result = normalize_batch(["+1 (415) 867-5309", "+14158675309"])
    assert len(result.identifiers) == 1
    assert result.identifiers[0].value == "+14158675309"


def test_valid_name_classified_and_whitespace_collapsed():
    ident, reason = validate_and_build("  Beau   Lebens  ")
    assert reason is None
    assert ident.type == IdentifierType.NAME
    assert ident.value == "Beau Lebens"


def test_name_preserves_casing_and_apostrophes():
    ident, reason = validate_and_build("Anne-Marie O'Brien")
    assert reason is None
    assert ident.type == IdentifierType.NAME
    assert ident.value == "Anne-Marie O'Brien"  # not forced to title case


def test_single_word_is_not_misclassified_as_name():
    # No space -> no basis to tell "Madonna" (a name) from a plain username.
    ident, reason = validate_and_build("Madonna")
    assert reason is None
    assert ident.type == IdentifierType.USERNAME


def test_name_supports_non_ascii_letters():
    ident, reason = validate_and_build("Rüzgar Karan Yönlü")
    assert reason is None
    assert ident.type == IdentifierType.NAME
    assert ident.value == "Rüzgar Karan Yönlü"
