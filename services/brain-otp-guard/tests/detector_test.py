from __future__ import annotations

from brain_otp_guard.detector import detect_otp

BANK_CODES = frozenset({"MTN", "ECOBANK", "GCB", "STANBIC"})


class TestShortCodeDetection:
    def test_bank_short_code_alone_is_otp(self) -> None:
        r = detect_otp(body=None, short_code="ECOBANK", bank_short_codes=BANK_CODES)
        assert r.is_otp is True
        assert r.matched_short_code == "ECOBANK"
        assert r.confidence >= 0.85

    def test_bank_short_code_case_insensitive(self) -> None:
        r = detect_otp(body=None, short_code="ecobank", bank_short_codes=BANK_CODES)
        assert r.is_otp is True
        assert r.matched_short_code == "ECOBANK"

    def test_bank_short_code_with_keyword_and_code_is_highest_confidence(self) -> None:
        r = detect_otp(
            body="Your OTP is 123456. Do not share with anyone.",
            short_code="ECOBANK",
            bank_short_codes=BANK_CODES,
        )
        assert r.is_otp is True
        assert r.matched_code == "123456"
        assert "otp" in r.matched_keywords
        assert r.confidence >= 0.95

    def test_unknown_short_code_with_no_body_is_not_otp(self) -> None:
        r = detect_otp(body=None, short_code="MARKETING", bank_short_codes=BANK_CODES)
        assert r.is_otp is False


class TestKeywordAndCodeDetection:
    def test_keyword_plus_code_fires(self) -> None:
        r = detect_otp(
            body="Your verification code is 4823. Reply STOP to opt out.",
            short_code=None,
            bank_short_codes=BANK_CODES,
        )
        assert r.is_otp is True
        assert r.matched_code == "4823"
        assert r.confidence >= 0.7

    def test_otp_keyword_with_code(self) -> None:
        r = detect_otp(
            body="OTP: 987654 valid for 5 minutes.",
            short_code=None,
            bank_short_codes=BANK_CODES,
        )
        assert r.is_otp is True
        assert r.matched_code == "987654"

    def test_pin_keyword_with_code(self) -> None:
        r = detect_otp(
            body="Your PIN for the transaction is 5678.",
            short_code=None,
            bank_short_codes=BANK_CODES,
        )
        assert r.is_otp is True

    def test_code_alone_is_not_otp(self) -> None:
        r = detect_otp(
            body="Order confirmed. Reference 4567. Thank you.",
            short_code=None,
            bank_short_codes=BANK_CODES,
        )
        assert r.is_otp is False

    def test_keyword_alone_without_code_or_short_code(self) -> None:
        r = detect_otp(
            body="Please verify your account.",
            short_code=None,
            bank_short_codes=BANK_CODES,
        )
        assert r.is_otp is False

    def test_two_keywords_without_code_treated_as_otp_low_confidence(self) -> None:
        r = detect_otp(
            body="Authentication: please do not share your verification code.",
            short_code=None,
            bank_short_codes=BANK_CODES,
        )
        assert r.is_otp is True
        assert r.confidence < 0.7

    def test_long_code_not_in_otp_range_does_not_match(self) -> None:
        r = detect_otp(
            body="OTP 123456789012",  # 12 digits — out of range
            short_code=None,
            bank_short_codes=BANK_CODES,
        )
        # No 4-8 digit standalone code → no fire on keyword+code path.
        # The long digit string is not a valid OTP code.
        assert r.matched_code is None

    def test_empty_body_and_no_short_code(self) -> None:
        r = detect_otp(body=None, short_code=None, bank_short_codes=BANK_CODES)
        assert r.is_otp is False
        assert r.matched_code is None
        assert r.matched_keywords == ()
