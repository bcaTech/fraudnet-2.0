"""Sanity checks on SignalEventV1."""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from fraudnet.schemas.signals import SignalEventV1
from fraudnet.schemas.types import EntityKind, RiskScore, Severity, Subject


def _signal(**overrides: object) -> SignalEventV1:
    base: dict[str, object] = {
        "event_id": "sig_test_0001",
        "event_ts_ms": 1_700_000_000_000,
        "ingest_ts_ms": 1_700_000_000_500,
        "source": "brain-behavioural",
        "signal_kind": "voice.velocity_burst",
        "subject": Subject(kind=EntityKind.NUMBER, id="+233241234567"),
        "score": RiskScore(
            value=0.82,
            model_id="behavioural",
            model_version="2026-04-01",
            computed_at_ms=1_700_000_000_400,
        ),
        "severity": Severity.HIGH,
    }
    base.update(overrides)
    return SignalEventV1.model_validate(base)


def test_signal_minimal() -> None:
    s = _signal()
    assert s.topic == "fraud.signals.v1"
    assert s.suppression_key is None
    assert s.severity == Severity.HIGH


def test_signal_with_evidence_and_suppression() -> None:
    s = _signal(
        evidence={"vel_1m": 12, "vel_5m": 47, "fanout_1h": 95},
        suppression_key="mtn-ghana:number:+233241234567:voice.velocity_burst",
    )
    assert s.evidence["vel_1m"] == 12
    assert s.suppression_key is not None


def test_signal_extra_fields_forbidden() -> None:
    with pytest.raises(PydanticValidationError):
        _signal(unknown="x")


def test_score_value_clamped_at_construction() -> None:
    with pytest.raises(PydanticValidationError):
        _signal(
            score=RiskScore(
                value=2.0,
                model_id="x",
                model_version="y",
                computed_at_ms=0,
            )
        )
