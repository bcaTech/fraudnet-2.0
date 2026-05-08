from __future__ import annotations

import asyncio
from time import time

from fraudnet.kafka import AvroConsumer, DLQRouter, KafkaSettings
from fraudnet.kafka.consumer import ConsumedMessage
from fraudnet.obs import counter, get_logger
from fraudnet.schemas.events import DecisionDispatchedV1
from action_tier2.actuators import ActuatorRegistry

_log = get_logger("action_tier2.runner")

_HANDLED = counter(
    "action_tier2_handled_total",
    "Tier-2 decisions handled.",
    labelnames=("action", "outcome"),
)


class Tier2Runner:
    def __init__(
        self,
        *,
        registry: ActuatorRegistry,
        kafka_settings_factory,
    ) -> None:
        self._registry = registry
        self._make_settings = kafka_settings_factory
        self._stop = asyncio.Event()
        self._consumer: object | None = None

    async def start(self) -> None:
        consumer = AvroConsumer(
            settings=self._make_settings("action-tier2-consumer"),
            topic="action.tier2.v1",
            model_cls=DecisionDispatchedV1,
            dlq=DLQRouter(self._make_settings("action-tier2-dlq")),
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
                "tier2.no_actuator", action=decision.action, decision_id=decision.decision_id
            )
            _HANDLED.labels(action=decision.action, outcome="failed").inc()
            return

        start = time()
        result = await actuator.execute(decision)
        latency_ms = int((time() - start) * 1000)
        _HANDLED.labels(action=decision.action, outcome=result.outcome).inc()

        _log.info(
            "tier2.action_taken",
            action=decision.action,
            outcome=result.outcome,
            latency_ms=latency_ms,
            error=result.error,
        )


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
