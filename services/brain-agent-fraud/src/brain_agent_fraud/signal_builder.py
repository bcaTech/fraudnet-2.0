"""Convert Detection → SignalEventV1 with XAI attached.

Mirrors `brain_behavioural.scorer.to_signal` so the on-the-wire shape is
identical: every fraud signal carries feature_contributions and a
human-readable explanation.
"""

from __future__ import annotations

from time import time
from uuid import uuid4

from fraudnet.schemas import SignalEventV1
from fraudnet.schemas.types import EntityKind, RiskScore, Severity, Subject
from fraudnet.xai import (
    StaticBaselineProvider,
    contributions_from_evidence,
    explain_signal,
    rank_contributions,
)

from brain_agent_fraud.detectors import Detection

MODEL_ID = "agent-fraud-rules"
MODEL_VERSION = "0.1.0"

# Agent-fraud features have their own baselines — high `cycles` is
# unusual; even one is suspicious. The static baselines are small
# integers so the contribution ranker can produce sensible weights.
_AGENT_BASELINES = StaticBaselineProvider(
    baselines={
        "cycles": 1,
        "cycle_amount_minor": 100_000,
        "pieces": 1,
        "total_minor": 100_000,
        "phantom_counterparties": 1,
        "phantom_ratio": 0.2,
        "cohort_size": 1,
        "shared_device_count": 1,
        "sequential_txn_count": 1,
        "peak_float_minor": 5_000_000,
        "internal_moves": 1,
    }
)

_SEVERITY_BY_NAME = {
    "low": Severity.LOW,
    "medium": Severity.MEDIUM,
    "high": Severity.HIGH,
    "critical": Severity.CRITICAL,
}


def to_signal(
    *,
    detection: Detection,
    agent_msisdn: str,
    source: str,
    tenant_id: str = "mtn-ghana",
) -> SignalEventV1:
    """Materialise a SignalEventV1 from an agent-fraud detection.

    `agent_msisdn` identifies the agent (their wallet's owner number).
    Agent fraud is naturally subject-keyed on the agent, not the
    counterparty.
    """
    now_ms = int(time() * 1000)
    score = RiskScore(
        value=detection.score,
        model_id=MODEL_ID,
        model_version=MODEL_VERSION,
        computed_at_ms=now_ms,
    )
    contribs = contributions_from_evidence(
        dict(detection.evidence),
        baselines=_AGENT_BASELINES,
        boost_features=detection.boost_features,
    )
    top = rank_contributions(contribs, top_n=3)
    explanation = explain_signal(
        signal_kind=detection.signal_kind,
        score=detection.score,
        top_contributions=top,
    )
    suppression_key = (
        f"{tenant_id}:number:{agent_msisdn}:{detection.signal_kind}"
    )
    return SignalEventV1(
        event_id=f"sig_{uuid4().hex[:24]}",
        event_ts_ms=now_ms,
        ingest_ts_ms=now_ms,
        source=source,
        tenant_id=tenant_id,
        signal_kind=detection.signal_kind,
        subject=Subject(kind=EntityKind.NUMBER, id=agent_msisdn),
        score=score,
        severity=_SEVERITY_BY_NAME.get(detection.severity, Severity.HIGH),
        evidence=detection.evidence,
        suppression_key=suppression_key,
        feature_contributions=top,
        explanation_text=explanation,
    )
