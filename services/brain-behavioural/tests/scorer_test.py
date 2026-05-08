from __future__ import annotations

from fraudnet.features.snapshot import NumberFeatures, WalletFeatures
from fraudnet.schemas.types import EntityKind, Severity
from brain_behavioural.scorer import HeuristicScorer, to_signal


class TestNumberScoring:
    def test_voice_velocity_burst_fires(self) -> None:
        nf = NumberFeatures(
            msisdn="+233241234567",
            velocity_1m=12,
            fanout_1h=80,
            velocity_1h=200,
        )
        result = HeuristicScorer().score_number(nf)
        assert result.signal_kind == "voice.velocity_burst"
        assert result.severity == Severity.HIGH
        assert result.score.value > 0.9

    def test_imei_churn_fires(self) -> None:
        nf = NumberFeatures(msisdn="+233241234567", imei_count=5)
        r = HeuristicScorer().score_number(nf)
        assert r.signal_kind == "device.imei_churn"

    def test_sms_bulk_template_fires(self) -> None:
        nf = NumberFeatures(
            msisdn="+233241234567",
            sms_freq_1h=40,
            sms_template_top="sha256:scam-pattern",
        )
        r = HeuristicScorer().score_number(nf)
        assert r.signal_kind == "sms.bulk_template"

    def test_sub_threshold_no_signal(self) -> None:
        nf = NumberFeatures(msisdn="+233241234567", velocity_1m=2, fanout_1h=3)
        r = HeuristicScorer().score_number(nf)
        assert r.signal_kind is None
        assert r.severity == Severity.LOW
        assert r.score.value < 0.5


class TestWalletScoring:
    def test_mule_velocity_fires(self) -> None:
        wf = WalletFeatures(
            wallet_id="W:1",
            txn_velocity_1h=20,
            counterparty_diversity_24h=12,
        )
        r = HeuristicScorer().score_wallet(wf)
        assert r.signal_kind == "momo.mule_velocity"
        assert r.severity == Severity.HIGH

    def test_high_value_velocity_fires(self) -> None:
        wf = WalletFeatures(
            wallet_id="W:1",
            txn_velocity_1h=10,
            counterparty_diversity_24h=2,
            value_p95_24h=500_000,
        )
        r = HeuristicScorer().score_wallet(wf)
        assert r.signal_kind == "momo.high_value_velocity"

    def test_sub_threshold_no_signal(self) -> None:
        wf = WalletFeatures(wallet_id="W:1", txn_velocity_1h=2)
        r = HeuristicScorer().score_wallet(wf)
        assert r.signal_kind is None


class TestToSignal:
    def test_signal_carries_suppression_key(self) -> None:
        nf = NumberFeatures(msisdn="+233241234567", velocity_1m=12, fanout_1h=80)
        result = HeuristicScorer().score_number(nf)
        sig = to_signal(
            result=result,
            subject_kind=EntityKind.NUMBER,
            subject_id="+233241234567",
            source="t",
        )
        assert sig is not None
        assert sig.suppression_key == "mtn-ghana:number:+233241234567:voice.velocity_burst"
        assert sig.signal_kind == "voice.velocity_burst"

    def test_no_signal_when_sub_threshold(self) -> None:
        nf = NumberFeatures(msisdn="+233241234567")
        result = HeuristicScorer().score_number(nf)
        sig = to_signal(
            result=result,
            subject_kind=EntityKind.NUMBER,
            subject_id="+233241234567",
            source="t",
        )
        assert sig is None
