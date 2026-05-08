"""Fuzzy name matching primitives.

Hand-rolled to avoid an extra dependency for what is small, well-known
code:

  - **Jaro-Winkler** for string similarity (high tolerance for typos
    and transposition; standard choice for name matching).
  - **Soundex** for phonetic matching (US-centric but the simplest
    phonetic algorithm; works for the bulk of Latin-script names).
  - **Metaphone** as a secondary phonetic check (catches names that
    Soundex normalises to different codes — e.g. "Schwartz" / "Swartz").
  - **Tokenised compose**: candidate name and query name are normalised
    (lowercase, strip punctuation, collapse whitespace) and matched
    token-by-token; the highest pairing wins. This handles surname-first
    vs given-first ordering without a separate parser.

A match score is a weighted blend of the three signals, mapped to [0, 1].
The default threshold (0.85) is the operator-tunable cut-off above which
the matcher reports a hit.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


_PUNCT_RE = re.compile(r"[^\w\s]")


def normalise(name: str) -> str:
    """Lowercase + strip diacritics + drop punctuation + collapse spaces.

    Matches on `Mensah-Boateng` and `Mensah Boateng` should be equal.
    """
    folded = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in folded if not unicodedata.combining(c))
    no_punct = _PUNCT_RE.sub(" ", ascii_only.lower())
    return " ".join(no_punct.split())


def tokens(name: str) -> list[str]:
    return [t for t in normalise(name).split() if t]


# ---------------------------------------------------------------------------
# Jaro-Winkler
# ---------------------------------------------------------------------------


def jaro_similarity(s1: str, s2: str) -> float:
    """Pure Jaro similarity in [0, 1]."""
    if s1 == s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    match_distance = max(len(s1), len(s2)) // 2 - 1
    s1_matches = [False] * len(s1)
    s2_matches = [False] * len(s2)
    matches = 0
    for i, c1 in enumerate(s1):
        start = max(0, i - match_distance)
        end = min(i + match_distance + 1, len(s2))
        for j in range(start, end):
            if s2_matches[j] or s2[j] != c1:
                continue
            s1_matches[i] = True
            s2_matches[j] = True
            matches += 1
            break
    if matches == 0:
        return 0.0
    transpositions = 0
    k = 0
    for i in range(len(s1)):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1
    transpositions //= 2
    return (
        matches / len(s1)
        + matches / len(s2)
        + (matches - transpositions) / matches
    ) / 3


def jaro_winkler(s1: str, s2: str, *, prefix_scale: float = 0.1) -> float:
    """Jaro-Winkler. Weights matching prefixes up to 4 chars."""
    j = jaro_similarity(s1, s2)
    prefix_len = 0
    for a, b in zip(s1, s2):
        if a != b:
            break
        prefix_len += 1
        if prefix_len == 4:
            break
    return j + prefix_len * prefix_scale * (1 - j)


# ---------------------------------------------------------------------------
# Soundex
# ---------------------------------------------------------------------------


_SOUNDEX_MAP = {
    "B": "1", "F": "1", "P": "1", "V": "1",
    "C": "2", "G": "2", "J": "2", "K": "2", "Q": "2", "S": "2", "X": "2", "Z": "2",
    "D": "3", "T": "3",
    "L": "4",
    "M": "5", "N": "5",
    "R": "6",
}


def soundex(name: str) -> str:
    """Standard Soundex — letter + 3 digits. Returns empty string for
    non-alphabetic input."""
    word = "".join(c for c in name.upper() if c.isalpha())
    if not word:
        return ""
    first = word[0]
    encoded = first
    last_code = _SOUNDEX_MAP.get(first, "")
    for ch in word[1:]:
        code = _SOUNDEX_MAP.get(ch, "")
        if code and code != last_code:
            encoded += code
        last_code = code if code else ""
        if len(encoded) == 4:
            break
    return (encoded + "000")[:4]


# ---------------------------------------------------------------------------
# Metaphone (simplified)
# ---------------------------------------------------------------------------


_VOWELS = set("AEIOU")


def metaphone(name: str) -> str:
    """Simplified Metaphone implementation.

    Not the original Lawrence Philips spec end-to-end, but covers the
    common rules: silent letters, vowels only at start, common
    digraphs (TH, SH, CH, PH, WR, WH, GN, KN). Length bounded at 6.
    """
    s = "".join(c for c in name.upper() if c.isalpha())
    if not s:
        return ""

    # Silent leading clusters
    for prefix, replace in (
        ("AE", "E"), ("GN", "N"), ("KN", "N"), ("PN", "N"), ("WR", "R"), ("WH", "W"),
    ):
        if s.startswith(prefix):
            s = replace + s[len(prefix):]
            break

    out: list[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        nxt = s[i + 1] if i + 1 < len(s) else ""
        if ch in _VOWELS:
            if i == 0:
                out.append(ch)
            i += 1
            continue
        if ch == "B":
            out.append("B")
        elif ch == "C":
            if nxt in ("E", "I", "Y"):
                out.append("S")
            elif nxt == "H":
                out.append("X")
                i += 1
            else:
                out.append("K")
        elif ch == "D":
            if nxt == "G" and i + 2 < len(s) and s[i + 2] in ("E", "I", "Y"):
                out.append("J")
                i += 1
            else:
                out.append("T")
        elif ch == "F":
            out.append("F")
        elif ch == "G":
            if nxt in ("E", "I", "Y"):
                out.append("J")
            elif nxt == "H":
                out.append("F")
                i += 1
            else:
                out.append("K")
        elif ch == "H":
            if not out or out[-1] not in _VOWELS:
                pass
            else:
                out.append("H")
        elif ch == "J":
            out.append("J")
        elif ch == "K":
            out.append("K")
        elif ch == "L":
            out.append("L")
        elif ch == "M":
            out.append("M")
        elif ch == "N":
            out.append("N")
        elif ch == "P":
            if nxt == "H":
                out.append("F")
                i += 1
            else:
                out.append("P")
        elif ch == "Q":
            out.append("K")
        elif ch == "R":
            out.append("R")
        elif ch == "S":
            if nxt == "H":
                out.append("X")
                i += 1
            else:
                out.append("S")
        elif ch == "T":
            if nxt == "H":
                out.append("0")  # TH → "0" in classic Metaphone
                i += 1
            else:
                out.append("T")
        elif ch == "V":
            out.append("F")
        elif ch == "W":
            if nxt in _VOWELS:
                out.append("W")
        elif ch == "X":
            out.append("KS")
        elif ch == "Y":
            if nxt in _VOWELS:
                out.append("Y")
        elif ch == "Z":
            out.append("S")
        i += 1
    return "".join(out)[:6]


# ---------------------------------------------------------------------------
# Composite name match
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NameMatch:
    score: float
    jaro_winkler_score: float
    phonetic_match: bool
    soundex_match: bool
    metaphone_match: bool
    matched_tokens: tuple[tuple[str, str], ...] = ()


def name_match_score(query: str, candidate: str) -> NameMatch:
    """Composite (jaro-winkler + phonetic) similarity over tokens.

    The score is `jw * 0.7 + phonetic_bonus`, where `phonetic_bonus` is
    0.15 for Soundex match plus 0.15 for Metaphone match. The cap is 1.0.

    Why not 100% Jaro-Winkler: phonetic agreement on a name with low
    edit-distance overlap (e.g. "Smith" vs "Smyth") is a meaningful
    signal that string distance alone misses. Conversely, near-identical
    strings whose phonetics differ (e.g. "Lee" vs "Leo") shouldn't
    *only* be judged by phonetics.
    """
    q_tokens = tokens(query)
    c_tokens = tokens(candidate)
    if not q_tokens or not c_tokens:
        return NameMatch(0.0, 0.0, False, False, False)

    # Best pairwise alignment (greedy on highest jw — fine for typical
    # 1-3 token names; on 5+ tokens this is suboptimal but cheap).
    pairs: list[tuple[str, str, float]] = []
    used_c: set[int] = set()
    for q in q_tokens:
        best = -1.0
        best_ci = -1
        for ci, c in enumerate(c_tokens):
            if ci in used_c:
                continue
            jw = jaro_winkler(q, c)
            if jw > best:
                best = jw
                best_ci = ci
        if best_ci >= 0:
            pairs.append((q, c_tokens[best_ci], best))
            used_c.add(best_ci)
    if not pairs:
        return NameMatch(0.0, 0.0, False, False, False)
    avg_jw = sum(p[2] for p in pairs) / len(pairs)

    # Phonetic agreement on the surname-most-likely token (the longest
    # one). Caters for name ordering without parsing.
    q_main = max(q_tokens, key=len)
    c_main = max(c_tokens, key=len)
    soundex_match = soundex(q_main) == soundex(c_main) and soundex(q_main) != ""
    metaphone_match = metaphone(q_main) == metaphone(c_main) and metaphone(q_main) != ""

    bonus = 0.0
    if soundex_match:
        bonus += 0.15
    if metaphone_match:
        bonus += 0.15
    score = min(1.0, avg_jw * 0.7 + bonus)
    return NameMatch(
        score=score,
        jaro_winkler_score=avg_jw,
        phonetic_match=soundex_match or metaphone_match,
        soundex_match=soundex_match,
        metaphone_match=metaphone_match,
        matched_tokens=tuple((p[0], p[1]) for p in pairs),
    )
