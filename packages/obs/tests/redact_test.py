"""Redact behaviour. PII must never reach a log line raw."""

from __future__ import annotations

import pytest

from fraudnet.obs.redact import redact, redact_mapping, scrub_text


class TestRedactValue:
    def test_msisdn_keeps_prefix_and_tail(self) -> None:
        out = redact("+233241234567")
        assert out.startswith("+233")
        assert out.endswith("67")
        assert "1234" not in out

    def test_local_msisdn_redacted(self) -> None:
        out = redact("0241234567")
        assert "1234" not in out
        assert out.endswith("67")

    @pytest.mark.parametrize("v", [None, "", " "])
    def test_empty_inputs(self, v: object) -> None:
        out = redact(v)
        assert out in {"<none>", "<empty>", "<redacted:1>"}

    def test_non_phone_strings_redacted(self) -> None:
        # We choose to over-redact rather than risk a leak.
        out = redact("some-secret-token-blah")
        assert "secret" not in out.lower()


class TestRedactMapping:
    def test_redacts_known_pii_keys(self) -> None:
        m = {"msisdn": "+233241234567", "name": "Kofi", "wallet_id": "W:1234"}
        out = redact_mapping(m)
        assert out["name"] == "Kofi"
        assert out["msisdn"] != "+233241234567"
        assert out["wallet_id"] != "W:1234"

    def test_recurses_into_nested(self) -> None:
        m = {"actor": {"msisdn": "0241234567"}, "ok": True}
        out = redact_mapping(m)
        assert out["actor"]["msisdn"] != "0241234567"
        assert out["ok"] is True

    def test_extra_keys_redacted(self) -> None:
        out = redact_mapping({"customer_ref": "abc"}, extra=frozenset({"customer_ref"}))
        assert out["customer_ref"] != "abc"

    def test_does_not_mutate_original(self) -> None:
        m = {"msisdn": "0241234567"}
        redact_mapping(m)
        assert m["msisdn"] == "0241234567"


class TestScrubText:
    def test_scrubs_e164(self) -> None:
        assert "+233241234567" not in scrub_text("call from +233241234567 reported")

    def test_scrubs_local_msisdn(self) -> None:
        assert "0241234567" not in scrub_text("number 0241234567 reported")

    def test_scrubs_long_token(self) -> None:
        text = "auth bearer abcdef0123456789abcdef0123456789abcdef"
        out = scrub_text(text)
        assert "abcdef0123456789abcdef0123456789" not in out
