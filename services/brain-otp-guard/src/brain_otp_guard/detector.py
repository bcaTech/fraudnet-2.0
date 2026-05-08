"""OTP-during-call correlation.

Pure detection logic: given an inbound MT SMS that looks like an OTP, plus
the recipient's active-call state, decide whether to fire the
`otp.during_call` signal. Stateless w.r.t. the registry — the registry
adapter is injected and is mocked in tests.

Heuristic for "is this an OTP SMS":

1.  Sender short-code matches a configured bank/fintech list (strongest).
2.  Body contains an OTP keyword (`OTP`, `PIN`, `verification code`,
    `transaction`, etc.) AND a 4-8 digit code.
3.  Body contains a 4-8 digit code AND any one of the OTP keywords.

We deliberately accept some false positives at the *content* level — the
final signal only fires when an OTP-shaped SMS coincides with an active
inbound call. The conjunction is what makes this high-precision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

# 4–8 digit code, surrounded by non-digit boundaries.
_OTP_CODE_RE: Final[re.Pattern[str]] = re.compile(r"(?:^|[^0-9])(\d{4,8})(?:[^0-9]|$)")

# Lowercased keywords that strongly suggest OTP / banking context.
_OTP_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "otp",
        "one time password",
        "one-time password",
        "verification code",
        "verification",
        "verify",
        "transaction",
        "authentication",
        "auth code",
        "secure code",
        "pin",
        "passcode",
        "do not share",
        "code is",
        "your code",
    }
)


@dataclass(frozen=True)
class OtpDetectionResult:
    is_otp: bool
    matched_code: str | None
    matched_keywords: tuple[str, ...]
    matched_short_code: str | None
    confidence: float  # 0.0..1.0 — how confident we are this SMS is OTP


def detect_otp(
    *,
    body: str | None,
    short_code: str | None,
    bank_short_codes: frozenset[str],
) -> OtpDetectionResult:
    """Decide whether an MT SMS looks like an OTP / banking SMS.

    The function does not look at the recipient's call state — that
    correlation happens in the runner. Confidence is derived from how many
    of (short-code, keywords, digit-code) hit.
    """
    sc_norm = short_code.upper() if short_code else None
    sc_match = sc_norm if sc_norm and sc_norm in bank_short_codes else None

    body_l = body.lower() if body else ""
    matched_keywords: tuple[str, ...] = tuple(kw for kw in _OTP_KEYWORDS if kw in body_l)

    code_match: str | None = None
    if body:
        m = _OTP_CODE_RE.search(body)
        if m:
            code_match = m.group(1)

    # Scoring rules:
    #  - Bank short-code alone is enough (banks rarely send non-OTP from short codes)
    #  - Keyword + numeric code is enough
    #  - Keyword OR code alone is not enough — too many false positives
    confidence = 0.0
    is_otp = False
    if sc_match is not None:
        confidence = 0.85
        is_otp = True
        if matched_keywords and code_match:
            confidence = 0.97
        elif matched_keywords or code_match:
            confidence = 0.92
    elif matched_keywords and code_match:
        confidence = 0.80
        is_otp = True
    elif len(matched_keywords) >= 2:
        # Multiple OTP keywords without a code — still suspicious; treat
        # as OTP context but lower confidence.
        confidence = 0.55
        is_otp = True

    return OtpDetectionResult(
        is_otp=is_otp,
        matched_code=code_match,
        matched_keywords=matched_keywords,
        matched_short_code=sc_match,
        confidence=confidence,
    )
