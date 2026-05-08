"""Async classifier runner.

Consumes sms.events.v1 directly (not graph.mutations.v1) because content is
SMS-specific. Per spec §5.3, the fast path uses hash lookups; the model
path needs the body when authorised.
"""

from __future__ import annotations

import asyncio

from fraudnet.kafka import AvroConsumer, AvroProducer, DLQRouter, KafkaSettings
from fraudnet.kafka.consumer import ConsumedMessage
from fraudnet.obs import counter, get_logger
from fraudnet.schemas.events import SmsEventV1
from fraudnet.schemas.signals import SignalEventV1
from brain_content.classifier import ContentClassifier, to_signal

_log = get_logger("brain_content.runner")

_CLASSIFIED = counter(
    "brain_content_classified_total",
    "SMS events classified.",
    labelnames=("fired", "with_body"),
)


class ContentRunner:
    def __init__(
        self,
        *,
        classifier: ContentClassifier,
        signal_producer: AvroProducer[SignalEventV1],
        kafka_settings_factory,
    ) -> None:
        self._classifier = classifier
        self._producer = signal_producer
        self._make_settings = kafka_settings_factory
        self._stop = asyncio.Event()
        self._consumer: object | None = None

    async def start(self) -> None:
        consumer = AvroConsumer(
            settings=self._make_settings("brain-content-sms"),
            topic="sms.events.v1",
            model_cls=SmsEventV1,
            dlq=DLQRouter(self._make_settings("brain-content-dlq")),
        )
        self._consumer = consumer
        await consumer.run(self._on_sms)

    async def stop(self) -> None:
        self._stop.set()
        if self._consumer is not None:
            self._consumer.stop()  # type: ignore[attr-defined]
        await self._producer.stop()

    async def _on_sms(self, msg: ConsumedMessage[SmsEventV1]) -> None:
        ev = msg.payload
        # MO and DR don't carry inbound spam; skip them. MT events are the
        # primary smishing surface.
        if ev.kind != "mt":
            return
        result = self._classifier.classify(
            body=ev.body,
            body_hash=ev.body_hash,
            template_hash=ev.template_hash,
        )
        signal = to_signal(
            result=result,
            sender_msisdn=ev.sender,
            source="brain-content",
            tenant_id=ev.tenant_id,
        )
        fired = signal is not None
        _CLASSIFIED.labels(
            fired=str(fired).lower(),
            with_body=str(ev.body is not None).lower(),
        ).inc()
        if signal is not None:
            await self._producer.send(signal, key=ev.sender)


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
