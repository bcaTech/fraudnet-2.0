"""Populator mapping: signal_kind → (intel_kind, identifier)."""

from __future__ import annotations

from time import time
from typing import Any

import pytest

from fraudnet.schemas import (
    EntityKind,
    RiskScore,
    Severity,
    SignalEventV1,
    Subject,
)
from intel_repository.populator import _SIGNAL_TO_INTEL


def _signal(*, signal_kind: str, evidence: dict[str, Any] | None = None) -> SignalEventV1:
    now = int(time() * 1000)
    return SignalEventV1(
        event_id="sig_test_test_test_test_x12",
        event_ts_ms=now,
        ingest_ts_ms=now,
        source="brain-x",
        signal_kind=signal_kind,
        subject=Subject(kind=EntityKind.NUMBER, id="+233200000001"),
        score=RiskScore(
            value=0.9,
            model_id="m",
            model_version="0",
            computed_at_ms=now,
        ),
        severity=Severity.HIGH,
        evidence=evidence or {},
        suppression_key=f"mtn-ghana:number:+233200000001:{signal_kind}",
    )


@pytest.mark.parametrize(
    ("signal_kind", "expected_intel_kind"),
    [
        ("voice.velocity_burst", "suspect_number"),
        ("sms.bulk_template", "suspect_number"),
        ("device.imei_churn", "suspect_number"),
        ("aml.watchlist_match", "suspect_number"),
        ("agent.commission_farming", "agent_risk"),
        ("agent.collusion", "agent_risk"),
    ],
)
def test_signal_maps_to_intel_kind(signal_kind: str, expected_intel_kind: str) -> None:
    intel_kind, extractor = _SIGNAL_TO_INTEL[signal_kind]
    assert intel_kind == expected_intel_kind
    assert extractor(_signal(signal_kind=signal_kind)) == "+233200000001"


def test_scam_template_uses_template_hash_evidence() -> None:
    intel_kind, extractor = _SIGNAL_TO_INTEL["sms.template_smishing"]
    sig = _signal(
        signal_kind="sms.template_smishing",
        evidence={"template_hash": "abc123def"},
    )
    assert intel_kind == "scam_template"
    assert extractor(sig) == "abc123def"


def test_scam_template_skips_when_no_hash_evidence() -> None:
    """Without a template_hash / body_hash the populator can't index — the
    extractor returns None and the signal is dropped (with a metric)."""
    _, extractor = _SIGNAL_TO_INTEL["sms.template_smishing"]
    sig = _signal(signal_kind="sms.template_smishing", evidence={})
    assert extractor(sig) is None


def test_unmapped_signal_kind_is_skipped() -> None:
    """A signal_kind not in the mapping is silently dropped."""
    assert "voice.unknown" not in _SIGNAL_TO_INTEL
