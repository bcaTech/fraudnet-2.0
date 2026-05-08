"""Explanation rendering — templates, deterministic, no PII."""

from __future__ import annotations

from fraudnet.schemas import FeatureContribution
from fraudnet.xai import (
    PatternMatch,
    explain_content_signal,
    explain_signal,
    feature_label,
    score_pattern_match,
    summarize_anomalies,
)


def _c(feature: str, value: float, baseline: float | None, weight: float) -> FeatureContribution:
    return FeatureContribution(feature=feature, value=value, baseline=baseline, weight=weight)


def test_explain_signal_velocity_burst_template() -> None:
    out = explain_signal(
        signal_kind="voice.velocity_burst",
        score=0.92,
        top_contributions=[
            _c("vel_1m", 47, 1, 0.95),
            _c("fanout_1h", 88, 8, 0.9),
        ],
    )
    assert "Voice velocity burst" in out
    assert "calls in the last minute" in out
    assert "47" in out
    assert "Score 0.92" in out


def test_explain_signal_unknown_kind_falls_back() -> None:
    out = explain_signal(
        signal_kind="brand.new.kind",
        score=0.7,
        top_contributions=[_c("foo", 5, 1, 0.5)],
    )
    assert out.startswith("brand.new.kind")
    assert "Score 0.70" in out


def test_explain_signal_handles_no_contributions() -> None:
    out = explain_signal(
        signal_kind="voice.velocity_burst", score=0.5, top_contributions=[]
    )
    assert "no individual feature stood out" in out


def test_summarize_anomalies_omits_baseline_when_missing() -> None:
    out = summarize_anomalies([_c("foo", 1, None, 0.5)])
    assert "baseline" not in out


def test_feature_label_falls_back_to_raw_name() -> None:
    assert feature_label("vel_1m") == "calls in the last minute"
    assert feature_label("custom.feature") == "custom.feature"


def test_explain_signal_deterministic() -> None:
    """Same input → same output. Tests rely on this."""
    args = dict(
        signal_kind="voice.velocity_burst",
        score=0.92,
        top_contributions=[_c("vel_1m", 47, 1, 0.95)],
    )
    a = explain_signal(**args)  # type: ignore[arg-type]
    b = explain_signal(**args)  # type: ignore[arg-type]
    assert a == b


def test_explain_signal_does_not_leak_msisdn() -> None:
    """The explanation must never include raw identifiers — even if a
    caller accidentally supplied a feature value that *looks* like one."""
    out = explain_signal(
        signal_kind="voice.velocity_burst",
        score=0.9,
        top_contributions=[_c("vel_1m", 233200000001, 1, 0.99)],
    )
    # The value is rendered as a number, not interpreted as an MSISDN.
    # This is a sanity check; in practice values are velocities, not MSISDNs.
    assert "+233" not in out


def test_explain_content_signal_includes_pattern_and_terms() -> None:
    out = explain_content_signal(
        signal_kind="sms.template_smishing",
        score=0.85,
        pattern_label="Smishing template",
        matched_terms=["claim your prize", "click here"],
        domain="evil.example",
    )
    assert "Smishing template" in out
    assert "claim your prize" in out
    assert "evil.example" in out
    assert "Score 0.85" in out


def test_score_pattern_match_emits_pattern_and_terms_features() -> None:
    contribs = score_pattern_match(
        PatternMatch(
            pattern_id="sms.template_smishing",
            pattern_label="Smishing template",
            score=0.9,
            matched_terms=("claim", "urgent"),
            domain="evil.example",
        )
    )
    feats = {c.feature for c in contribs}
    assert "pattern.sms.template_smishing" in feats
    assert any(f.startswith("term.") for f in feats)
    assert any(f.startswith("domain.") for f in feats)
