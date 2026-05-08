"""xai_block_for_signal — convert SignalEventV1 to alerts.details.xai shape."""

from __future__ import annotations

from time import time

from fraudnet.schemas import (
    EntityKind,
    FeatureContribution,
    RiskScore,
    Severity,
    SignalEventV1,
    Subject,
)
from fraudnet.xai import xai_block_for_signal


def _signal(*, with_xai: bool) -> SignalEventV1:
    now = int(time() * 1000)
    extra: dict = {}
    if with_xai:
        extra["feature_contributions"] = [
            FeatureContribution(feature="vel_1m", value=47, baseline=1, weight=0.95),
        ]
        extra["explanation_text"] = "Voice velocity burst — score 0.92."
    return SignalEventV1(
        event_id="sig_test_event_id_value_xx",
        event_ts_ms=now,
        ingest_ts_ms=now,
        source="brain-behavioural",
        signal_kind="voice.velocity_burst",
        subject=Subject(kind=EntityKind.NUMBER, id="+233200000001"),
        score=RiskScore(
            value=0.92,
            model_id="behavioural-heuristic",
            model_version="0.1.0",
            computed_at_ms=now,
        ),
        severity=Severity.HIGH,
        evidence={"vel_1m": 47},
        suppression_key="mtn-ghana:number:+233200000001:voice.velocity_burst",
        **extra,
    )


def test_xai_block_returned_when_signal_has_xai() -> None:
    block = xai_block_for_signal(_signal(with_xai=True))
    assert block is not None
    assert block["explanation_text"].startswith("Voice velocity burst")
    assert block["model_id"] == "behavioural-heuristic"
    assert block["top_features"][0]["feature"] == "vel_1m"
    assert block["top_features"][0]["weight"] == 0.95


def test_xai_block_none_when_signal_has_no_xai() -> None:
    """Signals without XAI must not pollute alerts.details with empties."""
    assert xai_block_for_signal(_signal(with_xai=False)) is None
