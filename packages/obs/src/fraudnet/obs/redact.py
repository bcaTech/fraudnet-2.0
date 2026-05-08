"""PII redaction for log lines and event metadata.

CLAUDE.md §7.4: "PII is redacted at the logging layer; the redact() function
is automatic for known field names (msisdn, imei, wallet_id, etc.) and
explicit elsewhere."

This is the runtime enforcement complement to the pre-commit lint rule in
scripts/lint_no_pii_logs.py — defence in depth against the easy mistake.
"""

from __future__ import annotations

import re
from typing import Any, Final

# Field names whose values are auto-redacted in any structlog event_dict.
_PII_FIELD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "msisdn",
        "phone",
        "phone_number",
        "caller",
        "callee",
        "sender",
        "recipient",
        "imsi",
        "imei",
        "wallet_id",
        "sender_wallet_id",
        "recipient_wallet_id",
        "account",
        "account_number",
        "account_hash",
        "card",
        "card_number",
        "pin",
        "password",
        "token",
        "secret",
        "ssn",
        "national_id",
    }
)

# Compiled patterns for free-text scan. Conservative — false positives are
# acceptable; false negatives are not.
_PATTERNS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    # Ghanaian E.164 (+233 + 9 digits) and other E.164 (+1..+99)
    (re.compile(r"\+\d{8,15}"), "[+REDACTED-MSISDN]"),
    # Local Ghanaian mobile (0XX XXX XXXX, 10 digits starting with 02 or 05)
    (re.compile(r"\b0[25]\d{8}\b"), "[REDACTED-MSISDN]"),
    # IMEI 14–17 digits
    (re.compile(r"\b\d{14,17}\b"), "[REDACTED-DIGITS]"),
    # Likely API tokens (long base64-ish runs)
    (re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"), "[REDACTED-TOKEN]"),
)


def redact(value: object) -> str:
    """Redact a single value for safe logging.

    Strategy: keep length signal (helpful for debugging) but never emit the
    raw value. For phone-number-like inputs, keep the country code prefix and
    the last 2 digits to preserve some debug usefulness; redact the middle.
    """
    if value is None:
        return "<none>"
    s = str(value)
    if not s:
        return "<empty>"

    # MSISDN-shaped: keep prefix and tail
    if s.startswith("+") and s[1:].isdigit() and 8 <= len(s[1:]) <= 15:
        return f"{s[:4]}****{s[-2:]}"
    if s.isdigit() and len(s) >= 10:
        return f"{s[:2]}****{s[-2:]}"

    # General: hash-like length cue without content
    return f"<redacted:{len(s)}>"


def redact_mapping(mapping: dict[str, Any], extra: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Return a copy of the mapping with known-PII keys redacted.

    Use `extra` to redact additional keys at a call site. Never mutates the
    original mapping.
    """
    pii = _PII_FIELD_NAMES | extra
    out: dict[str, Any] = {}
    for k, v in mapping.items():
        if k.lower() in pii:
            out[k] = redact(v)
        elif isinstance(v, dict):
            out[k] = redact_mapping(v, extra)
        else:
            out[k] = v
    return out


def scrub_text(text: str) -> str:
    """Aggressively scrub free-text strings (e.g. SMS bodies in error logs).

    Token-class redaction; drops MSISDN, IMEI, and high-entropy token-like
    runs. The cost is occasional collateral redaction of innocent strings —
    cheaper than a leak.
    """
    out = text
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out
