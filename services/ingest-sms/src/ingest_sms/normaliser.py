"""SMS body normalisation, hashing, URL extraction, template fingerprinting.

The transforms here are cheap, deterministic, and run on every SMS — even
when body capture is disabled (we still emit body_hash + template_hash from
the body so downstream can dedup and cluster without re-reading bodies).

Algorithms:
  - body_hash: SHA-256 of NFC-normalised, whitespace-collapsed body. Hash
    survives encoding wobble between SMSC vendors.
  - template_hash: SHA-256 of body with digit/MSISDN/amount runs replaced
    by class tokens. Identical promo or scam templates collide on this hash
    even when the variable parts differ.
  - URLs: best-effort regex extraction of http(s):// links. Domains
    lowercased; trailing punctuation stripped.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

# Variable-part tokens. Order matters — phone numbers before generic digits.
_MSISDN_RE = re.compile(r"\b(?:\+?\d{8,15}|0\d{9})\b")
_AMOUNT_RE = re.compile(r"\b(?:GHS|GH₵|GHC|\$|£|€)?\s?\d{1,3}(?:[,.\s]\d{3})*(?:[.,]\d{1,2})?\b")
_DIGIT_RE = re.compile(r"\b\d+\b")
_WHITESPACE_RE = re.compile(r"\s+")
_URL_RE = re.compile(r"https?://[^\s<>\"')\]}]+", re.IGNORECASE)

_TRAILING_PUNCT = ".,;:!?)]}\"'"


@dataclass(frozen=True)
class NormalisedSms:
    body_hash: str
    template_hash: str
    urls: tuple[str, ...]


def normalise(body: str) -> NormalisedSms:
    if not body:
        empty = hashlib.sha256(b"").hexdigest()
        return NormalisedSms(body_hash=empty, template_hash=empty, urls=())

    nfc = unicodedata.normalize("NFC", body)
    collapsed = _WHITESPACE_RE.sub(" ", nfc).strip()

    body_hash = "sha256:" + hashlib.sha256(collapsed.encode("utf-8")).hexdigest()

    # Templatise: variable parts replaced by class tokens; case-folded.
    templ = collapsed.casefold()
    templ = _MSISDN_RE.sub("<MSISDN>", templ)
    templ = _AMOUNT_RE.sub("<AMOUNT>", templ)
    templ = _DIGIT_RE.sub("<NUM>", templ)
    template_hash = "sha256:" + hashlib.sha256(templ.encode("utf-8")).hexdigest()

    urls = tuple(sorted({_clean_url(u) for u in _URL_RE.findall(collapsed)}))

    return NormalisedSms(body_hash=body_hash, template_hash=template_hash, urls=urls)


def _clean_url(url: str) -> str:
    while url and url[-1] in _TRAILING_PUNCT:
        url = url[:-1]
    # Lowercase scheme + host while preserving path case (paths can be
    # case-sensitive). Cheap split on the first '/'.
    if "://" in url:
        scheme, rest = url.split("://", 1)
        if "/" in rest:
            host, path = rest.split("/", 1)
            return f"{scheme.lower()}://{host.lower()}/{path}"
        return f"{scheme.lower()}://{rest.lower()}"
    return url.lower()
