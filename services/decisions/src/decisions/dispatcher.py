"""Build DecisionDispatchedV1 from a signal/motif + outcome and fan out to
the audit topic + the per-tier action topic.
"""

from __future__ import annotations

from time import time
from uuid import uuid4

from fraudnet.kafka import AvroProducer
from fraudnet.obs import counter, get_logger
from fraudnet.schemas.events import DecisionDispatchedV1, MotifDetectedV1
from fraudnet.schemas.signals import SignalEventV1
from fraudnet.schemas.types import LatencyTier, Subject
from decisions.policy import DispatchOutcome, Policy

_log = get_logger("decisions.dispatcher")

_DISPATCHED = counter(
    "decisions_dispatched_total",
    "Decisions dispatched.",
    labelnames=("tier", "action", "rule_id"),
)


# Per-tier topic mapping (DECISIONS.md D-003).
_TIER_TOPIC = {
    LatencyTier.TIER1_INLINE: "action.tier1.v1",
    LatencyTier.TIER2_NRT: "action.tier2.v1",
    LatencyTier.TIER3_INVESTIGATION: "action.tier3.v1",
}


class DecisionDispatcher:
    """Fans a single decision out to two topics:

    1. `decisions.dispatched.v1` — audit trail of every decision (compliance).
    2. `action.tier{1,2,3}.v1` — the actuator's per-tier topic.

    Both writes go through dedicated AvroProducers so partition assignment
    and retention can diverge per-topic in production.
    """

    def __init__(
        self,
        *,
        audit_producer: AvroProducer[DecisionDispatchedV1],
        tier_producers: dict[LatencyTier, AvroProducer[DecisionDispatchedV1]],
        policy: Policy,
    ) -> None:
        self._audit = audit_producer
        self._tier = tier_producers
        self._policy = policy

    async def dispatch_signal(
        self, sig: SignalEventV1, outcome: DispatchOutcome
    ) -> DecisionDispatchedV1:
        decision = self._build_decision(
            outcome=outcome,
            subject=sig.subject,
            severity=sig.severity,
            score=sig.score,
            tenant_id=sig.tenant_id,
            source=f"decisions:{sig.signal_kind}",
            suppression_key=sig.suppression_key,
            metadata={"signal_id": sig.event_id, "signal_kind": sig.signal_kind},
        )
        await self._publish(decision)
        return decision

    async def dispatch_motif(
        self, m: MotifDetectedV1, outcome: DispatchOutcome
    ) -> DecisionDispatchedV1:
        # Use the first member as the canonical subject for motif-driven
        # decisions; downstream actuators look at metadata['motif'] to fetch
        # the full member set when needed.
        if not m.members:
            raise ValueError("motif event has no members")
        subject = m.members[0]
        from fraudnet.schemas.types import Severity

        severity = Severity.HIGH  # motifs default to high; refined per rule in Phase 2
        decision = self._build_decision(
            outcome=outcome,
            subject=subject,
            severity=severity,
            score=m.score,
            tenant_id=m.tenant_id,
            source=f"decisions:motif:{m.motif}",
            suppression_key=f"{m.tenant_id}:motif:{m.motif}:{subject.id}",
            metadata={
                "motif": m.motif,
                "motif_event_id": m.event_id,
                "member_count": len(m.members),
            },
        )
        await self._publish(decision)
        return decision

    def _build_decision(
        self,
        *,
        outcome: DispatchOutcome,
        subject: Subject,
        severity,
        score,
        tenant_id: str,
        source: str,
        suppression_key: str | None,
        metadata: dict[str, str | int | float | bool],
    ) -> DecisionDispatchedV1:
        now_ms = int(time() * 1000)
        return DecisionDispatchedV1(
            event_id=f"dec_{uuid4().hex[:24]}",
            event_ts_ms=now_ms,
            ingest_ts_ms=now_ms,
            source=source,
            tenant_id=tenant_id,
            decision_id=f"dec_{uuid4().hex[:24]}",
            tier=outcome.tier,
            action=outcome.action,
            subject=subject,
            severity=severity,
            score=score,
            policy_id=self._policy.id,
            policy_version=self._policy.version,
            suppression_key=suppression_key,
            metadata={
                **metadata,
                "rule_id": outcome.rule_id,
                "policy_fingerprint": self._policy.fingerprint(),
            },
        )

    async def _publish(self, decision: DecisionDispatchedV1) -> None:
        # Audit-trail (always)
        await self._audit.send(decision, key=decision.subject.id)
        # Per-tier topic
        producer = self._tier.get(decision.tier)
        if producer is None:
            _log.error("decisions.no_producer_for_tier", tier=decision.tier.value)
            return
        await producer.send(decision, key=decision.subject.id)
        _DISPATCHED.labels(
            tier=decision.tier.value,
            action=decision.action,
            rule_id=str(decision.metadata.get("rule_id", "?")),
        ).inc()


def topic_for_tier(tier: LatencyTier) -> str:
    return _TIER_TOPIC[tier]
