"""Fraud signal — async scored output from brain-* services.

Per DECISIONS.md D-004: brain services publish to `fraud.signals.v1` and the
decisions service consumes. This is the asynchronous edge of the scoring
pipeline; the synchronous gRPC scoring path stays available for the inline
tier where decisions has to block on a score.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from fraudnet.schemas.types import RiskScore, Severity, Subject


class SignalEventV1(BaseModel):
    """One scored signal from a brain-* service.

    Multiple signals per subject are expected over time. The decisions
    service applies suppression on `suppression_key` to avoid alert storms.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    topic: ClassVar[str] = "fraud.signals.v1"

    event_id: str = Field(min_length=8, max_length=64)
    event_ts_ms: int = Field(ge=0)
    ingest_ts_ms: int = Field(ge=0)
    source: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(default="mtn-ghana", min_length=1)

    signal_kind: str = Field(min_length=1, max_length=64)  # e.g. 'voice.velocity_burst'
    subject: Subject
    score: RiskScore
    severity: Severity

    # Optional structured evidence (feature attribution, top URLs, anomaly
    # vectors). Keep keys primitive — these flow into Iceberg.
    evidence: dict[str, str | int | float | bool] = Field(default_factory=dict)

    # Suppression key lets decisions dedup repeat alerts. Conventional shape:
    # "<tenant>:<subject_kind>:<subject_id>:<signal_kind>".
    suppression_key: str | None = None
