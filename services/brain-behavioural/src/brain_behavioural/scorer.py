"""Scoring interface.

Phase 1 implementation is a hand-coded heuristic. Phase 2 swaps to a
LightGBM-trained model behind the same interface (DECISIONS.md D-006). All
scoring goes through `Scorer.score_*`; nothing else gets to compute scores.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from time import time
from uuid import uuid4

from fraudnet.features.snapshot import NumberFeatures, WalletFeatures
from fraudnet.schemas.signals import SignalEventV1
from fraudnet.schemas.types import EntityKind, RiskScore, Severity, Subject

MODEL_ID = "behavioural-heuristic"
MODEL_VERSION = "0.1.0"


@dataclass(frozen=True)
class ScoringResult:
    score: RiskScore
    signal_kind: str | None  # None when below threshold
    severity: Severity
    evidence: dict[str, str | int | float | bool]


class Scorer(ABC):
    @abstractmethod
    def score_number(self, features: NumberFeatures) -> ScoringResult: ...

    @abstractmethod
    def score_wallet(self, features: WalletFeatures) -> ScoringResult: ...


class HeuristicScorer(Scorer):
    """Phase 1 heuristic. Thresholds tuned against the Airtel-style smishing
    profile that drove the spec. Replaceable in Phase 2 with a LightGBM
    artefact loaded by the model registry.
    """

    def score_number(self, f: NumberFeatures) -> ScoringResult:
        evidence: dict[str, str | int | float | bool] = {
            "vel_1m": f.velocity_1m,
            "vel_5m": f.velocity_5m,
            "vel_1h": f.velocity_1h,
            "fanout_1h": f.fanout_1h,
            "imei_count": f.imei_count,
            "sms_freq_1h": f.sms_freq_1h,
        }

        # Voice velocity burst — wangiri / robocall pattern
        if f.velocity_1m >= 10 and f.fanout_1h >= 50:
            return ScoringResult(
                score=_score(0.92, evidence),
                signal_kind="voice.velocity_burst",
                severity=Severity.HIGH,
                evidence=evidence,
            )

        # SIM/IMEI churn — possible compromise
        if f.imei_count >= 4:
            return ScoringResult(
                score=_score(0.78, evidence),
                signal_kind="device.imei_churn",
                severity=Severity.MEDIUM,
                evidence=evidence,
            )

        # SMS bulk template — possible smishing operator
        if f.sms_freq_1h >= 30 and f.sms_template_top is not None:
            return ScoringResult(
                score=_score(0.85, evidence),
                signal_kind="sms.bulk_template",
                severity=Severity.HIGH,
                evidence=evidence,
            )

        # Sub-threshold — emit a low score with no signal_kind
        elevated = (
            (f.velocity_5m >= 30) + (f.fanout_1h >= 20) + (f.imei_count >= 2)
        )
        score_value = min(0.99, 0.1 + 0.18 * elevated)
        return ScoringResult(
            score=_score(score_value, evidence),
            signal_kind=None,
            severity=Severity.LOW,
            evidence=evidence,
        )

    def score_wallet(self, f: WalletFeatures) -> ScoringResult:
        evidence: dict[str, str | int | float | bool] = {
            "txn_velocity_1h": f.txn_velocity_1h,
            "counterparty_diversity_24h": f.counterparty_diversity_24h,
            "value_p95_24h": f.value_p95_24h,
        }

        # Mule pattern: high velocity + high counterparty diversity in 24h
        if f.txn_velocity_1h >= 15 and f.counterparty_diversity_24h >= 8:
            return ScoringResult(
                score=_score(0.9, evidence),
                signal_kind="momo.mule_velocity",
                severity=Severity.HIGH,
                evidence=evidence,
            )

        # Cash-in/cash-out arbitrage: high p95 amount with high velocity
        if f.value_p95_24h >= 100_000 and f.txn_velocity_1h >= 8:
            return ScoringResult(
                score=_score(0.82, evidence),
                signal_kind="momo.high_value_velocity",
                severity=Severity.MEDIUM,
                evidence=evidence,
            )

        return ScoringResult(
            score=_score(0.1, evidence),
            signal_kind=None,
            severity=Severity.LOW,
            evidence=evidence,
        )


def _score(value: float, evidence: dict[str, str | int | float | bool]) -> RiskScore:
    return RiskScore(
        value=value,
        model_id=MODEL_ID,
        model_version=MODEL_VERSION,
        computed_at_ms=int(time() * 1000),
        feature_attribution={k: float(v) for k, v in evidence.items() if isinstance(v, (int, float))},
    )


def to_signal(
    *,
    result: ScoringResult,
    subject_kind: EntityKind,
    subject_id: str,
    source: str,
    tenant_id: str = "mtn-ghana",
) -> SignalEventV1 | None:
    """Materialise a SignalEventV1 from a scoring result. Returns None if
    the result was sub-threshold (signal_kind is None).
    """
    if result.signal_kind is None:
        return None
    now_ms = int(time() * 1000)
    subject = Subject(kind=subject_kind, id=subject_id)
    suppression_key = f"{tenant_id}:{subject_kind.value}:{subject_id}:{result.signal_kind}"
    return SignalEventV1(
        event_id=f"sig_{uuid4().hex[:24]}",
        event_ts_ms=now_ms,
        ingest_ts_ms=now_ms,
        source=source,
        tenant_id=tenant_id,
        signal_kind=result.signal_kind,
        subject=subject,
        score=result.score,
        severity=result.severity,
        evidence=result.evidence,
        suppression_key=suppression_key,
    )
