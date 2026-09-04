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


def test_deep_false_is_unchanged_from_default():
    # deep=False must behave identically to omitting the argument entirely --
    # quick/normal depth must never see the extra swapped-order form.
    assert variants("Çağlar Öztürk", deep=False) == variants("Çağlar Öztürk")
    assert variants("John Doe", deep=False) == variants("John Doe")


def test_deep_true_adds_a_third_swapped_order_form():
    result = variants("Rüzgar Yönlü", deep=True)

    assert result == ["Rüzgar Yönlü", "Ruzgar Yonlu", "Yonlu Ruzgar"]


def test_deep_true_is_bounded_to_three_forms():
    assert len(variants("Rüzgar Karan Yönlü", deep=True)) <= 3


def test_deep_true_skips_swap_for_single_word_name():
    # A single-word "name" (already rejected by NAME_RE upstream in practice,
    # but variants() itself shouldn't crash or duplicate on it) has nothing
    # to swap.
    assert variants("Madonna", deep=True) == ["Madonna"]


def test_deep_true_does_not_duplicate_when_swap_equals_an_existing_form():
    # A palindrome-order two-word ASCII name: folded form has no diacritics
    # to strip, and swapping "John John" produces the same string again.
    assert variants("John John", deep=True) == ["John John"]
