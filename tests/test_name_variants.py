from osintrecon.core.name_variants import ascii_fold, variants


def test_ascii_fold_turkish_surname():
    assert ascii_fold("Yönlü") == "Yonlu"


def test_ascii_fold_turkish_full_name():
    assert ascii_fold("Çağlar Öztürk") == "Caglar Ozturk"


def test_ascii_fold_dotless_and_dotted_i():
    assert ascii_fold("Ahmet Şahin") == "Ahmet Sahin"
    assert ascii_fold("İstanbul Işık") == "Istanbul Isik"


def test_ascii_fold_already_ascii_is_unchanged():
    assert ascii_fold("John Doe") == "John Doe"


def test_ascii_fold_collapses_whitespace():
    assert ascii_fold("Rüzgar  Karan   Yönlü") == "Ruzgar Karan Yonlu"


def test_variants_skips_duplicate_when_already_ascii():
    assert variants("John Doe") == ["John Doe"]


def test_variants_includes_original_and_folded_form():
    result = variants("Çağlar Öztürk")
    assert result == ["Çağlar Öztürk", "Caglar Ozturk"]


def test_variants_is_bounded_to_two_forms():
    assert len(variants("Rüzgar Karan Yönlü")) <= 2
