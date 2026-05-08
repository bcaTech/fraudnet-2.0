"""Kafka-backed audit writer.

Service code wires this in at startup:

    producer = AvroProducer(
        settings=KafkaSettings.from_env(client_id=service_name),
        model_cls=AuditEventV1,
    )
    await producer.start()
    configure_audit_writer(KafkaAuditWriter(producer))
"""

from __future__ import annotations

from fraudnet.audit.record import AuditWriter
from fraudnet.kafka.producer import AvroProducer
from fraudnet.schemas.audit import AuditEventV1


class KafkaAuditWriter(AuditWriter):
    def __init__(self, producer: AvroProducer[AuditEventV1]) -> None:
        self._producer = producer

    async def write(self, event: AuditEventV1) -> None:
        # Key by tenant_id so audit reads partition cleanly per tenant.
        await self._producer.send(event, key=event.tenant_id)
