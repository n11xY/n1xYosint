"""Generates a small, bounded set of alternate forms for a full name, so
NAME-accepting plugins can retry a search that returned nothing under the
exact form the user typed.

The concrete motivating case is Turkish names: academic/dev-platform
indexes are frequently ASCII-only, so "Yönlü" may need to be tried as
"Yonlu" to match. The fold is Unicode NFKD-based (decompose each letter
into base + combining diacritic, then drop the diacritic), not a
hardcoded Turkish table -- it works the same way for any language's
accented letters, Turkish's dotless/dotted i pair being the one exception
NFKD alone doesn't handle (ı and İ don't decompose that way), so those
two get an explicit substitution first.

Deliberately NOT a "query expansion" system with a config knob (see the
plan this was built from) -- every NAME plugin just tries the exact form
first and only spends a second request on the ASCII-folded variant if
that came back empty. No new CLI flag, no combinatorial query blow-up.
"""
from __future__ import annotations

import unicodedata

# NFKD doesn't decompose these two Turkish letters the way it does e.g.
# ö -> o + combining diaeresis -- substitute them explicitly first.
_TURKISH_PRE_FOLD = str.maketrans({"ı": "i", "İ": "I"})


def ascii_fold(name: str) -> str:
    """Best-effort transliteration to plain ASCII letters (diacritics
    stripped), whitespace collapsed. "Çağlar Öztürk" -> "Caglar Ozturk"."""
    pre = name.translate(_TURKISH_PRE_FOLD)
    decomposed = unicodedata.normalize("NFKD", pre)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.split())


def variants(name: str, deep: bool = False) -> list[str]:
    """The exact name, then (only if different) its ASCII-folded form.
    Order matters: callers should try index 0 first and only spend a
    second request on index 1 if the first came back empty.

    When `deep=True` (the CLI's --search-depth deep), a third, bounded
    form is added: the word order reversed ("Yonlu Ruzgar" from "Ruzgar
    Yonlu") -- covers records indexed family-name-first. Still capped
    (max 3 forms) and still tried only if the earlier forms came back
    empty, so normal/quick-depth behavior (deep=False, the default) is
    unchanged."""
    forms = [name]
    folded = ascii_fold(name)
    if folded and folded != name:
        forms.append(folded)

    if deep:
        base_for_swap = folded or name
        words = base_for_swap.split()
        if len(words) >= 2:
            swapped = " ".join(reversed(words))
            if swapped not in forms:
                forms.append(swapped)

    return forms
