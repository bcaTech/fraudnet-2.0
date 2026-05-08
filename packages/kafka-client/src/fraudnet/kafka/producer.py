"""Avro producer with FraudNet wiring conventions.

Behaviour:
  - Validates the payload against the topic's Pydantic class before sending.
  - Serialises with Confluent's Avro serializer, registering the schema with
    the configured Schema Registry on first use.
  - Idempotent producer; exactly-once semantics on supported brokers.
  - Fail-closed if Schema Registry is unreachable: refuses to publish raw
    bytes (CLAUDE.md §6.3).
  - On delivery failure, raises DeliveryError after retries exhaust; the
    caller is responsible for routing to the DLQ via DLQRouter if appropriate.

Usage:
    producer = AvroProducer(
        settings=KafkaSettings.from_env(client_id="ingest-momo"),
        model_cls=MoMoEventV1,
    )
    await producer.start()
    await producer.send(event, key=event.sender_wallet_id or event.recipient_wallet_id)
    await producer.flush()
    await producer.stop()
"""

from __future__ import annotations

import asyncio
import json
from typing import Generic, TypeVar, cast

from confluent_kafka import Producer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroSerializer
from confluent_kafka.serialization import (
    MessageField,
    SerializationContext,
    StringSerializer,
)
from pydantic import BaseModel

from fraudnet.kafka.config import KafkaSettings
from fraudnet.kafka.errors import DeliveryError, KafkaConfigError, SchemaError
from fraudnet.obs import counter, get_logger
from fraudnet.schemas.avro_registry import avro_schema

T = TypeVar("T", bound=BaseModel)

_log = get_logger("fraudnet.kafka.producer")

_SENT = counter(
    "fraudnet_kafka_messages_sent_total",
    "Kafka messages successfully delivered.",
    labelnames=("topic",),
)
_FAILED = counter(
    "fraudnet_kafka_messages_failed_total",
    "Kafka messages that failed delivery.",
    labelnames=("topic", "reason"),
)


class AvroProducer(Generic[T]):
    """Avro-encoded Kafka producer for one Pydantic event class.

    One producer instance per topic. The class is fixed at construction time
    so we can validate payloads against it without runtime guessing.
    """

    def __init__(
        self,
        *,
        settings: KafkaSettings,
        model_cls: type[T],
        topic: str | None = None,
    ) -> None:
        topic_name = topic or getattr(model_cls, "topic", None)
        if not topic_name:
            raise KafkaConfigError(
                f"{model_cls.__name__} has no `topic` ClassVar; pass topic= explicitly"
            )
        self._topic: str = topic_name
        self._model_cls = model_cls
        self._settings = settings
        self._registry = SchemaRegistryClient({"url": settings.schema_registry_url})
        try:
            schema_dict = avro_schema(self._topic)
        except FileNotFoundError as exc:
            raise SchemaError(f"no Avro schema for topic {self._topic}") from exc
        self._serializer = AvroSerializer(
            schema_registry_client=self._registry,
            schema_str=json.dumps(schema_dict),
            conf={"auto.register.schemas": True},
        )
        self._key_serializer = StringSerializer("utf-8")
        self._producer = Producer(settings.producer_config())
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        # Force schema registration up front so we fail closed at startup if
        # the registry is unreachable, not in the middle of a hot path.
        try:
            self._registry.get_subjects()
        except Exception as exc:  # noqa: BLE001 — registry exposes generic exceptions
            raise KafkaConfigError(
                f"schema registry unreachable: {self._settings.schema_registry_url}"
            ) from exc

    async def send(self, payload: T, *, key: str | None = None) -> None:
        """Send a payload. Returns when the broker has acknowledged delivery."""
        if not isinstance(payload, self._model_cls):
            raise SchemaError(
                f"payload type mismatch: got {type(payload).__name__}, "
                f"expected {self._model_cls.__name__}"
            )
        if self._loop is None:
            raise KafkaConfigError("producer not started — call start() first")

        # Validation of the payload happened at construction (Pydantic). We
        # only need to dump to a dict for Avro serialisation.
        avro_value = self._serializer(
            payload.model_dump(mode="json"),
            SerializationContext(self._topic, MessageField.VALUE),
        )
        avro_key = (
            self._key_serializer(key, SerializationContext(self._topic, MessageField.KEY))
            if key is not None
            else None
        )

        future: asyncio.Future[None] = self._loop.create_future()

        def _on_delivery(err: object, _msg: object) -> None:
            assert self._loop is not None
            if err is not None:
                err_str = str(err)
                self._loop.call_soon_threadsafe(
                    future.set_exception,
                    DeliveryError(err_str, topic=self._topic, key=key),
                )
            else:
                self._loop.call_soon_threadsafe(future.set_result, None)

        self._producer.produce(
            topic=self._topic,
            value=avro_value,
            key=avro_key,
            on_delivery=_on_delivery,
        )
        # poll(0) services delivery callbacks without blocking.
        self._producer.poll(0)

        try:
            await future
            _SENT.labels(topic=self._topic).inc()
        except DeliveryError as exc:
            _FAILED.labels(topic=self._topic, reason="delivery").inc()
            _log.error("kafka.delivery_failed", topic=self._topic, error=str(exc))
            raise

    async def flush(self, timeout: float = 30.0) -> int:
        """Flush outstanding messages. Returns count of unflushed messages."""
        return cast(int, self._producer.flush(timeout))

    async def stop(self) -> None:
        await self.flush()
