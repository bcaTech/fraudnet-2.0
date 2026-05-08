"""Avro consumer.

Behaviour:
  - Manual commit only. Offsets advance after the handler returns successfully.
  - On handler exception, the message is routed to the DLQ via DLQRouter and
    the offset advances. The handler is NOT retried in-process — retries are
    a producer concern (downstream replay), not a consumer concern.
  - Lag is exposed via ConsumerLagProbe; healthchecks compose it.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar

from confluent_kafka import Consumer, KafkaException, Message
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroDeserializer
from confluent_kafka.serialization import MessageField, SerializationContext
from pydantic import BaseModel, ValidationError

from fraudnet.kafka.config import KafkaSettings
from fraudnet.kafka.dlq import DLQRouter
from fraudnet.kafka.errors import KafkaConfigError, SchemaError
from fraudnet.obs import counter, get_logger, observe_duration_async, request_duration
from fraudnet.schemas.avro_registry import avro_schema

T = TypeVar("T", bound=BaseModel)

_log = get_logger("fraudnet.kafka.consumer")

_RECEIVED = counter(
    "fraudnet_kafka_messages_received_total",
    "Kafka messages successfully consumed.",
    labelnames=("topic",),
)
_DLQ = counter(
    "fraudnet_kafka_messages_dlq_total",
    "Kafka messages routed to DLQ.",
    labelnames=("topic", "reason"),
)


ConsumerHandler = Callable[["ConsumedMessage[T]"], Awaitable[None]]


class ConsumedMessage(Generic[T]):
    __slots__ = ("payload", "key", "topic", "partition", "offset", "timestamp_ms")

    def __init__(
        self,
        *,
        payload: T,
        key: str | None,
        topic: str,
        partition: int,
        offset: int,
        timestamp_ms: int,
    ) -> None:
        self.payload = payload
        self.key = key
        self.topic = topic
        self.partition = partition
        self.offset = offset
        self.timestamp_ms = timestamp_ms


class AvroConsumer(Generic[T]):
    """Avro-decoded Kafka consumer for one Pydantic event class."""

    def __init__(
        self,
        *,
        settings: KafkaSettings,
        topic: str,
        model_cls: type[T],
        dlq: DLQRouter | None = None,
    ) -> None:
        self._topic = topic
        self._model_cls = model_cls
        self._settings = settings
        self._dlq = dlq
        self._registry = SchemaRegistryClient({"url": settings.schema_registry_url})
        try:
            schema_dict = avro_schema(self._topic)
        except FileNotFoundError as exc:
            raise SchemaError(f"no Avro schema for topic {topic}") from exc
        self._deserializer = AvroDeserializer(
            schema_registry_client=self._registry,
            schema_str=json.dumps(schema_dict),
        )
        self._consumer = Consumer(settings.consumer_config())
        self._consumer.subscribe([topic])
        self._stop = asyncio.Event()
        self._service = settings.client_id

    async def run(self, handler: ConsumerHandler[T]) -> None:
        loop = asyncio.get_running_loop()
        while not self._stop.is_set():
            msg = await loop.run_in_executor(None, self._poll_one)
            if msg is None:
                await asyncio.sleep(0.05)
                continue
            await self._dispatch(msg, handler)

    def _poll_one(self) -> Message | None:
        msg = self._consumer.poll(timeout=1.0)
        if msg is None:
            return None
        if msg.error() is not None:
            _log.warning("kafka.poll_error", error=str(msg.error()))
            return None
        return msg

    async def _dispatch(self, msg: Message, handler: ConsumerHandler[T]) -> None:
        topic = msg.topic() or self._topic
        partition = msg.partition()
        offset = msg.offset()
        try:
            value = self._deserializer(
                msg.value(),
                SerializationContext(topic, MessageField.VALUE),
            )
            payload = self._model_cls.model_validate(value)
        except (ValidationError, KafkaException, ValueError) as exc:
            _DLQ.labels(topic=topic, reason="decode_error").inc()
            _log.warning(
                "kafka.decode_failed",
                topic=topic,
                partition=partition,
                offset=offset,
                error=str(exc),
            )
            if self._dlq is not None:
                await self._dlq.send(
                    raw_value=msg.value(),
                    raw_key=msg.key(),
                    source_topic=topic,
                    reason="decode_error",
                    error=str(exc),
                )
            self._consumer.commit(msg)
            return

        key = msg.key().decode("utf-8") if msg.key() else None
        consumed = ConsumedMessage(
            payload=payload,
            key=key,
            topic=topic,
            partition=partition,
            offset=offset,
            timestamp_ms=msg.timestamp()[1] if msg.timestamp()[0] else 0,
        )

        try:
            async with observe_duration_async(
                request_duration,
                service=self._service,
                route=topic,
                method="consume",
                status="200",
            ):
                await handler(consumed)
            _RECEIVED.labels(topic=topic).inc()
        except Exception as exc:  # noqa: BLE001 — handler exceptions are caught explicitly so the consumer keeps running
            _DLQ.labels(topic=topic, reason="handler_error").inc()
            _log.exception(
                "kafka.handler_failed",
                topic=topic,
                partition=partition,
                offset=offset,
            )
            if self._dlq is not None:
                await self._dlq.send(
                    raw_value=msg.value(),
                    raw_key=msg.key(),
                    source_topic=topic,
                    reason="handler_error",
                    error=str(exc),
                )

        self._consumer.commit(msg)

    def stop(self) -> None:
        self._stop.set()
        self._consumer.close()

    @property
    def underlying(self) -> Consumer:
        """Escape hatch for ConsumerLagProbe — do not use elsewhere."""
        return self._consumer

    @property
    def topic(self) -> str:
        return self._topic
