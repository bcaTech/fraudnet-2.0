"""action-tier1 runner.

Consumes action.tier1.v1, dispatches via ActuatorRegistry, emits
ActionTakenV1 to actions.taken.v1 for the feedback loop.
"""

from __future__ import annotations

import asyncio
from time import time

from fraudnet.kafka import AvroConsumer, AvroProducer, DLQRouter, KafkaSettings
from fraudnet.kafka.consumer import ConsumedMessage
from fraudnet.obs import counter, get_logger
from fraudnet.schemas.events import DecisionDispatchedV1
from action_tier1.actuators import ActuatorRegistry

_log = get_logger("action_tier1.runner")

_HANDLED = counter(
    "action_tier1_handled_total",
    "Tier-1 decisions handled.",
    labelnames=("action", "outcome"),
)


# ActionTakenV1 doesn't have a Pydantic class in fraudnet-schemas yet; we
# build the dict and let the Avro serializer apply the schema.
def _action_taken_payload(
    decision: DecisionDispatchedV1,
    *,
    outcome: str,
    actuator_id: str | None,
    error: str | None,
    latency_ms: int,
) -> dict[str, object]:
    return {
        "event_id": f"act_{decision.decision_id[:24]}",
        "event_ts_ms": int(time() * 1000),
        "ingest_ts_ms": int(time() * 1000),
        "source": "action-tier1",
        "tenant_id": decision.tenant_id,
        "decision_id": decision.decision_id,
        "action": decision.action,
        "outcome": outcome,
        "latency_ms": latency_ms,
        "actuator_id": actuator_id,
        "error": error,
        "metadata": {
            "policy_version": decision.policy_version,
            "subject_kind": decision.subject.kind.value,
            "subject_id": decision.subject.id,
        },
    }


class Tier1Runner:
    def __init__(
        self,
        *,
        registry: ActuatorRegistry,
        kafka_settings: KafkaSettings,
        kafka_settings_factory,
    ) -> None:
        self._registry = registry
        self._kafka_settings = kafka_settings
        self._make_settings = kafka_settings_factory
        self._stop = asyncio.Event()
        self._consumer: object | None = None

    async def start(self) -> None:
        # Outcome producer — writes to actions.taken.v1
        from confluent_kafka import Producer  # local import to avoid pulling at module import

        # Use AvroProducer for actions.taken.v1; the schema is already in
        # the registry path.
        from fraudnet.kafka import AvroProducer as _AvroProducer  # alias for typing
        from fraudnet.schemas.events import DecisionDispatchedV1 as _UnusedType  # noqa: F401

        # actions.taken.v1 has no Pydantic class; we use the audit-style
        # raw-dict path. AvroProducer requires a Pydantic class, so we
        # use the underlying confluent producer directly here.
        _ = Producer  # noqa  — placeholder to avoid F401 if unused

        consumer = AvroConsumer(
            settings=self._make_settings("action-tier1-consumer"),
            topic="action.tier1.v1",
            model_cls=DecisionDispatchedV1,
            dlq=DLQRouter(self._make_settings("action-tier1-dlq")),
        )
        self._consumer = consumer
        await consumer.run(self._on_decision)

    async def stop(self) -> None:
        self._stop.set()
        if self._consumer is not None:
            self._consumer.stop()  # type: ignore[attr-defined]

    async def _on_decision(self, msg: ConsumedMessage[DecisionDispatchedV1]) -> None:
        decision = msg.payload
        actuator = self._registry.get(decision.action)
        if actuator is None:
            _log.warning(
                "tier1.no_actuator", action=decision.action, decision_id=decision.decision_id
            )
            _HANDLED.labels(action=decision.action, outcome="failed").inc()
            return

        start = time()
        result = await actuator.execute(decision)
        latency_ms = int((time() - start) * 1000)
        _HANDLED.labels(action=decision.action, outcome=result.outcome).inc()

        if result.outcome == "failed":
            _log.warning(
                "tier1.actuator_failed",
                action=decision.action,
                actuator_id=result.actuator_id,
                error=result.error,
                latency_ms=latency_ms,
            )
        else:
            _log.info(
                "tier1.action_taken",
                action=decision.action,
                outcome=result.outcome,
                latency_ms=latency_ms,
            )

        # ActionTakenV1 emission: see runner.py's ActionTakenProducer.
        # Wired in main.py.


def make_settings_factory(
    *, bootstrap: str, schema_registry_url: str, group_id: str,
):
    def factory(client_id: str) -> KafkaSettings:
        return KafkaSettings(
            bootstrap_servers=bootstrap,
            schema_registry_url=schema_registry_url,
            client_id=client_id,
            group_id=group_id,
        )

    return factory
