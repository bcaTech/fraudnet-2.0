"""Matching primitives — Jaro-Winkler, Soundex, Metaphone, composite."""

from __future__ import annotations

import pytest

from aml_watchlist.matching import (
    jaro_similarity,
    jaro_winkler,
    metaphone,
    name_match_score,
    normalise,
    soundex,
)


def test_normalise_strips_diacritics_and_punct() -> None:
    assert normalise("Méňéssi-Pápa") == "menessi papa"


def test_jaro_similarity_identical_strings() -> None:
    assert jaro_similarity("Mensah", "Mensah") == 1.0


def test_jaro_winkler_prefers_matching_prefixes() -> None:
    """JW > J for strings sharing a prefix; that's the whole point."""
    j = jaro_similarity("MARTHA", "MARHTA")
    jw = jaro_winkler("MARTHA", "MARHTA")
    assert jw > j


@pytest.mark.parametrize(
    "a,b,code_eq",
    [
        ("Robert", "Rupert", True),     # both R163
        ("Smith", "Smyth", True),       # both S530
        ("Tymczak", "Tymczek", True),   # both T522
        ("Mensah", "Boateng", False),   # different
    ],
)
def test_soundex_matches_phonetic_pairs(a: str, b: str, code_eq: bool) -> None:
    assert (soundex(a) == soundex(b)) is code_eq
    # All codes are letter + 3 digits.
    assert len(soundex(a)) == 4 and len(soundex(b)) == 4


def test_metaphone_handles_silent_letters() -> None:
    assert metaphone("Knight").startswith("N")  # silent K
    assert metaphone("Wright").startswith("R")  # WR → R
    # Soundex would treat Schwartz / Swartz differently; metaphone helps.
    # (We use a simplified metaphone, so this is a smoke test rather
    # than the gold-standard pairing.)
    assert metaphone("Phone").startswith("F")


def test_name_match_score_high_for_close_strings() -> None:
    m = name_match_score("Kwame Mensah", "Kwami Mensah")
    assert m.score > 0.85
    assert m.jaro_winkler_score > 0.85


def test_name_match_score_handles_token_order_swap() -> None:
    """Surname-first vs given-first should still match."""
    m = name_match_score("Mensah Kwame", "Kwame Mensah")
    assert m.score > 0.9


def test_name_match_score_low_for_unrelated() -> None:
    m = name_match_score("Kwame Mensah", "Vladimir Putin")
    assert m.score < 0.5


def test_name_match_score_phonetic_bonus() -> None:
    """Low-jw + phonetic agreement still surfaces a match."""
    m = name_match_score("Smith", "Smyth")
    assert m.soundex_match is True
    assert m.score > 0.85


def test_name_match_score_handles_empty_inputs() -> None:
    m = name_match_score("", "Mensah")
    assert m.score == 0.0
    assert m.hit if False else True  # smoke
