"""Tests for telco primitive types."""

from __future__ import annotations

import pytest

from fraudnet.schemas.types import MSISDN, EntityKind, Purpose, RiskScore


class TestMSISDN:
    def test_parses_local_ghanaian_format(self) -> None:
        assert MSISDN("0241234567") == "+233241234567"

    def test_parses_e164(self) -> None:
        assert MSISDN("+233241234567") == "+233241234567"

    def test_idempotent(self) -> None:
        once = MSISDN("0241234567")
        twice = MSISDN(once)
        assert once == twice
        assert isinstance(twice, MSISDN)

    @pytest.mark.parametrize("bad", ["", "abc", "12", "+1", "not-a-number"])
    def test_rejects_invalid(self, bad: str) -> None:
        with pytest.raises(ValueError):
            MSISDN(bad)

    def test_pydantic_round_trip(self) -> None:
        from pydantic import BaseModel

        class M(BaseModel):
            n: MSISDN

        m = M.model_validate({"n": "0241234567"})
        assert m.n == "+233241234567"
        assert m.model_dump() == {"n": "+233241234567"}


def test_entity_kind_values() -> None:
    assert EntityKind.NUMBER.value == "number"
    assert {e.value for e in EntityKind} == {"number", "wallet", "device", "account", "url"}


def test_purpose_membership() -> None:
    # The set of purposes is closed; new purposes require DPO sign-off and are
    # added to the enum. This test guards against accidental drift.
    assert {p.value for p in Purpose} == {
        "fraud_prevention",
        "regulatory_export",
        "audit",
        "incident_response",
    }


class TestRiskScore:
    def test_valid_score(self) -> None:
        score = RiskScore(
            value=0.85,
            model_id="brain-behavioural",
            model_version="2026-04-01",
            computed_at_ms=1_700_000_000_000,
        )
        assert score.value == 0.85

    def test_rejects_out_of_range(self) -> None:
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError):
            RiskScore(
                value=1.5,
                model_id="x",
                model_version="y",
                computed_at_ms=0,
            )
