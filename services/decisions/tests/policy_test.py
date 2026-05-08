from __future__ import annotations

from pathlib import Path

import pytest

from fraudnet.schemas.events import MotifDetectedV1
from fraudnet.schemas.signals import SignalEventV1
from fraudnet.schemas.types import EntityKind, LatencyTier, RiskScore, Severity, Subject
from decisions.policy import Policy, evaluate_motif, evaluate_signal


@pytest.fixture
def default_policy() -> Policy:
    here = Path(__file__).resolve().parent.parent / "policies" / "default.yaml"
    return Policy.load(here)


def _signal(**overrides: object) -> SignalEventV1:
    base: dict[str, object] = {
        "event_id": "sig_t",
        "event_ts_ms": 1_700_000_000_000,
        "ingest_ts_ms": 1_700_000_000_000,
        "source": "test",
        "signal_kind": "voice.velocity_burst",
        "subject": Subject(kind=EntityKind.NUMBER, id="+233241234567"),
        "score": RiskScore(value=0.92, model_id="x", model_version="y", computed_at_ms=0),
        "severity": Severity.HIGH,
    }
    base.update(overrides)
    return SignalEventV1.model_validate(base)


def _motif(**overrides: object) -> MotifDetectedV1:
    base: dict[str, object] = {
        "event_id": "m_t",
        "event_ts_ms": 1_700_000_000_000,
        "ingest_ts_ms": 1_700_000_000_000,
        "source": "test",
        "motif": "voice_sms_momo_24h",
        "members": [Subject(kind=EntityKind.NUMBER, id="+233241234567")],
        "confidence": 0.9,
        "score": RiskScore(value=0.85, model_id="x", model_version="y", computed_at_ms=0),
    }
    base.update(overrides)
    return MotifDetectedV1.model_validate(base)


class TestPolicyMatching:
    def test_voice_velocity_to_tier1(self, default_policy: Policy) -> None:
        outcome = evaluate_signal(default_policy, _signal())
        assert outcome.tier == LatencyTier.TIER1_INLINE
        assert outcome.action == "volte.tag_suspected_spam"
        assert outcome.suppression_window_s > 0

    def test_sms_smishing_to_tier2(self, default_policy: Policy) -> None:
        outcome = evaluate_signal(
            default_policy,
            _signal(signal_kind="sms.template_smishing", severity=Severity.MEDIUM),
        )
        assert outcome.tier == LatencyTier.TIER2_NRT

    def test_unknown_signal_falls_to_default(self, default_policy: Policy) -> None:
        outcome = evaluate_signal(
            default_policy,
            _signal(signal_kind="unknown.pattern"),
        )
        assert outcome.rule_id == "__default__"
        assert outcome.tier == LatencyTier.TIER3_INVESTIGATION

    def test_voice_velocity_low_severity_falls_through(self, default_policy: Policy) -> None:
        outcome = evaluate_signal(
            default_policy,
            _signal(severity=Severity.LOW),
        )
        # severity_in: [critical, high] in policy → low falls to default
        assert outcome.rule_id == "__default__"

    def test_motif_voice_sms_momo_to_tier1(self, default_policy: Policy) -> None:
        outcome = evaluate_motif(default_policy, _motif())
        assert outcome.tier == LatencyTier.TIER1_INLINE
        assert outcome.action == "investigation.escalate"

    def test_motif_below_score_threshold_falls_through(self, default_policy: Policy) -> None:
        outcome = evaluate_motif(
            default_policy,
            _motif(score=RiskScore(value=0.5, model_id="x", model_version="y", computed_at_ms=0)),
        )
        assert outcome.rule_id == "__default__"


def test_policy_fingerprint_stable(default_policy: Policy) -> None:
    assert default_policy.fingerprint() == default_policy.fingerprint()


def test_signal_match_does_not_match_motif_rule() -> None:
    p = Policy.from_dict(
        {
            "id": "t",
            "version": "1",
            "rules": [
                {"id": "m1", "match": {"motif": "voice_sms_momo_24h"},
                 "action": "x.y", "tier": "tier1"}
            ],
        }
    )
    out = evaluate_signal(p, _signal())
    assert out.rule_id == "__default__"
