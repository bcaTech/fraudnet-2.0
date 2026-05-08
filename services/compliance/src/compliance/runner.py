"""compliance runner — consumes audit + decisions, persists to Postgres."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from fraudnet.kafka import AvroConsumer, DLQRouter, KafkaSettings
from fraudnet.kafka.consumer import ConsumedMessage
from fraudnet.obs import counter, get_logger
from fraudnet.schemas.audit import AuditEventV1
from fraudnet.schemas.events import DecisionDispatchedV1
from compliance.store import AuditStore

KafkaSettingsFactory = Callable[[str], KafkaSettings]

_log = get_logger("compliance.runner")

_PERSISTED = counter(
    "compliance_persisted_total",
    "Audit / decision events persisted.",
    labelnames=("topic",),
)


class ComplianceRunner:
    def __init__(
        self,
        *,
        store: AuditStore,
        kafka_settings_factory: KafkaSettingsFactory,
    ) -> None:
        self._store = store
        self._make_settings = kafka_settings_factory
        self._stop = asyncio.Event()
        self._consumers: list[object] = []

    async def start(self) -> None:
        audits = AvroConsumer(
            settings=self._make_settings("compliance-audit"),
            topic="audit.events.v1",
            model_cls=AuditEventV1,
            dlq=DLQRouter(self._make_settings("compliance-dlq")),
        )
        decisions = AvroConsumer(
            settings=self._make_settings("compliance-decisions"),
            topic="decisions.dispatched.v1",
            model_cls=DecisionDispatchedV1,
            dlq=DLQRouter(self._make_settings("compliance-dlq")),
        )
        self._consumers = [audits, decisions]

        async with asyncio.TaskGroup() as tg:
            tg.create_task(audits.run(self._on_audit), name="consume-audit")
            tg.create_task(decisions.run(self._on_decision), name="consume-decisions")
            tg.create_task(self._stop.wait(), name="stop")

    async def stop(self) -> None:
        self._stop.set()
        for c in self._consumers:
            c.stop()  # type: ignore[attr-defined]
        await self._store.close()

    async def _on_audit(self, msg: ConsumedMessage[AuditEventV1]) -> None:
        await self._store.write_audit_event(msg.payload)
        _PERSISTED.labels(topic="audit.events.v1").inc()

    async def _on_decision(self, msg: ConsumedMessage[DecisionDispatchedV1]) -> None:
        await self._store.write_decision(msg.payload)
        _PERSISTED.labels(topic="decisions.dispatched.v1").inc()


def make_settings_factory(
    *, bootstrap: str, schema_registry_url: str, group_id: str
) -> KafkaSettingsFactory:
    def factory(client_id: str) -> KafkaSettings:
        return KafkaSettings(
            bootstrap_servers=bootstrap,
            schema_registry_url=schema_registry_url,
            client_id=client_id,
            group_id=group_id,
        )

    return factory
