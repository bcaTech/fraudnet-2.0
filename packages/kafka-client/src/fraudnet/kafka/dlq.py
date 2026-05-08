"""Dead-letter routing.

Per CLAUDE.md §5.1: every ingest source has a paired DLQ topic
(`*.dlq.v1`). When a downstream consumer cannot decode or process a message,
the raw value + reason + error metadata go to the DLQ. Manual replay tooling
(under `tools/replay`) reads from the DLQ.

DLQ messages are not Avro-encoded — the source payload may have been the
reason for the failure. We write JSON envelopes with the raw bytes
base64-encoded and the failure metadata structured.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import cast

from confluent_kafka import Producer

from fraudnet.kafka.config import KafkaSettings
from fraudnet.kafka.errors import DeliveryError
from fraudnet.obs import counter, get_logger

_log = get_logger("fraudnet.kafka.dlq")

_SENT = counter(
    "fraudnet_kafka_dlq_messages_total",
    "Messages routed to DLQ.",
    labelnames=("source_topic", "reason"),
)


class DLQRouter:
    """Routes failed messages to per-source DLQ topics.

    The mapping is deterministic: source topic `voice.events.v1` routes to
    `voice.events.dlq.v1`, etc.
    """

    def __init__(self, settings: KafkaSettings) -> None:
        cfg = settings.producer_config()
        # DLQ writes do not need exactly-once; we want them to land best-effort
        # and never block the upstream consumer.
        cfg["enable.idempotence"] = False
        cfg["acks"] = "1"
        self._producer = Producer(cfg)
        self._service = settings.client_id

    @staticmethod
    def dlq_for(source_topic: str) -> str:
        # Insert .dlq before the version suffix.
        if "." not in source_topic:
            return f"{source_topic}.dlq"
        head, _, tail = source_topic.rpartition(".")
        # tail is the version, e.g. 'v1'
        return f"{head}.dlq.{tail}"

    async def send(
        self,
        *,
        raw_value: bytes | None,
        raw_key: bytes | None,
        source_topic: str,
        reason: str,
        error: str,
    ) -> None:
        topic = self.dlq_for(source_topic)
        envelope = {
            "source_topic": source_topic,
            "reason": reason,
            "error": error,
            "service": self._service,
            "raw_value_b64": base64.b64encode(raw_value).decode("ascii") if raw_value else None,
            "raw_key_b64": base64.b64encode(raw_key).decode("ascii") if raw_key else None,
        }
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()

        def _on_delivery(err: object, _msg: object) -> None:
            if err is not None:
                loop.call_soon_threadsafe(
                    future.set_exception,
                    DeliveryError(str(err), topic=topic),
                )
            else:
                loop.call_soon_threadsafe(future.set_result, None)

        self._producer.produce(
            topic=topic,
            value=json.dumps(envelope).encode("utf-8"),
            on_delivery=_on_delivery,
        )
        self._producer.poll(0)
        try:
            await future
            _SENT.labels(source_topic=source_topic, reason=reason).inc()
        except DeliveryError:
            _log.error("dlq.delivery_failed", source_topic=source_topic, reason=reason)
            # Intentionally do not re-raise — the upstream consumer should
            # always make progress past a poison message even if the DLQ is
            # also down. The lag-aware health check will surface the issue.

    def flush(self, timeout: float = 10.0) -> int:
        return cast(int, self._producer.flush(timeout))
