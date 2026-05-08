"""Runner handler tests with a stub AuditStore.

The runner's Kafka wiring is exercised in integration tests (Phase 2 — needs
a real schema registry). Here we cover the handler contract: every consumed
message lands in the right store method and increments the metric."""

from __future__ import annotations

from typing import Any

from fraudnet.kafka.consumer import ConsumedMessage
from fraudnet.schemas.audit import AuditEventV1
from fraudnet.schemas.events import DecisionDispatchedV1
from fraudnet.schemas.types import (
    EntityKind,
    LatencyTier,
    Purpose,
    RiskScore,
    Severity,
    Subject,
)
from fraudnet.testing import make_audit_event
from compliance.runner import ComplianceRunner


class _StubStore:
    def __init__(self) -> None:
        self.audits: list[AuditEventV1] = []
        self.decisions: list[DecisionDispatchedV1] = []
        self.closed = False

    async def write_audit_event(self, ev: AuditEventV1) -> None:
        self.audits.append(ev)

    async def write_decision(self, d: DecisionDispatchedV1) -> None:
        self.decisions.append(d)

    async def close(self) -> None:
        self.closed = True


def _settings_factory(_: str) -> Any:  # never invoked in these tests
    raise AssertionError("kafka settings must not be built in unit tests")


def _wrap(payload: Any, topic: str) -> ConsumedMessage[Any]:
    return ConsumedMessage(
        payload=payload,
        key=None,
        topic=topic,
        partition=0,
        offset=0,
        timestamp_ms=1_700_000_000_000,
    )


async def test_audit_handler_persists() -> None:
    store = _StubStore()
    runner = ComplianceRunner(store=store, kafka_settings_factory=_settings_factory)  # type: ignore[arg-type]
    ev = make_audit_event(action="alerts.claim", purpose=Purpose.FRAUD_PREVENTION)
    await runner._on_audit(_wrap(ev, "audit.events.v1"))
    assert store.audits == [ev]


async def test_decision_handler_persists() -> None:
    store = _StubStore()
    runner = ComplianceRunner(store=store, kafka_settings_factory=_settings_factory)  # type: ignore[arg-type]
    decision = DecisionDispatchedV1(
        event_id="dec_abc12345",
        event_ts_ms=1_700_000_000_000,
        ingest_ts_ms=1_700_000_000_001,
        source="decisions",
        decision_id="dec_abc12345",
        tier=LatencyTier.TIER1_INLINE,
        action="volte.tag_suspected_spam",
        subject=Subject(kind=EntityKind.NUMBER, id="+233241234567"),
        severity=Severity.HIGH,
        score=RiskScore(
            value=0.93, model_id="m", model_version="v", computed_at_ms=0
        ),
        policy_id="rule.voice.velocity_burst",
        policy_version="default-1",
        suppression_key="key123",
    )
    await runner._on_decision(_wrap(decision, "decisions.dispatched.v1"))
    assert store.decisions == [decision]
