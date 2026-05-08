"""fraud.signals.v1 listener — pulls URL-related signals into the blocklist.

The brain-content service emits signals like `sms.malicious_url` with the
matched URL in `evidence.url_match`. This listener watches the topic and
adds the corresponding domain to the blocklist with a TTL so the entry
expires if the underlying signal stops firing.
"""

from __future__ import annotations

import asyncio

from fraudnet.kafka import AvroConsumer, DLQRouter, KafkaSettings
from fraudnet.kafka.consumer import ConsumedMessage
from fraudnet.obs import counter, get_logger
from fraudnet.schemas.signals import SignalEventV1
from url_intel.blocklist import Blocklist

_log = get_logger("url_intel.signals_listener")

_SIGNAL_INGESTED = counter(
    "url_intel_signals_ingested_total",
    "Signals consumed by url-intel.",
    labelnames=("signal_kind", "outcome"),
)


class SignalsListener:
    """Subscribe to fraud.signals.v1 and ingest URL signals."""

    def __init__(
        self,
        *,
        blocklist: Blocklist,
        kafka_settings_factory,
        ttl_s: int,
    ) -> None:
        self._bl = blocklist
        self._make_settings = kafka_settings_factory
        self._ttl_s = ttl_s
        self._consumer: AvroConsumer[SignalEventV1] | None = None

    async def start(self) -> None:
        consumer: AvroConsumer[SignalEventV1] = AvroConsumer(
            settings=self._make_settings("url-intel-signals"),
            topic="fraud.signals.v1",
            model_cls=SignalEventV1,
            dlq=DLQRouter(self._make_settings("url-intel-signals-dlq")),
        )
        self._consumer = consumer
        await consumer.run(self._on_signal)

    def stop(self) -> None:
        if self._consumer is not None:
            self._consumer.stop()

    async def _on_signal(self, msg: ConsumedMessage[SignalEventV1]) -> None:
        sig = msg.payload
        # Heuristic match: signal_kind contains "url", or evidence carries
        # a url_match. brain-content's "sms.malicious_url" is the primary
        # producer in Phase 1.
        url_value = self._extract_url(sig)
        if url_value is None:
            _SIGNAL_INGESTED.labels(signal_kind=sig.signal_kind, outcome="skipped").inc()
            return
        added, reason = await self._bl.add(
            domain=url_value,
            source=f"signals:{sig.signal_kind}",
            category=str(sig.evidence.get("url_category") or "phishing"),
            confidence=float(sig.score.value),
            ttl_s=self._ttl_s,
        )
        _SIGNAL_INGESTED.labels(
            signal_kind=sig.signal_kind, outcome="added" if added else reason
        ).inc()

    @staticmethod
    def _extract_url(sig: SignalEventV1) -> str | None:
        kind = sig.signal_kind.lower()
        if "url" not in kind and sig.subject.kind.value != "url":
            # Allow URL subjects through even without "url" in the signal_kind.
            return None
        # Prefer explicit url_match in evidence; fallback to subject id.
        url_match = sig.evidence.get("url_match")
        if isinstance(url_match, str) and url_match:
            return url_match
        if sig.subject.kind.value == "url":
            return sig.subject.id
        return None


def make_settings_factory(*, bootstrap: str, schema_registry_url: str, group_id: str):
    def factory(client_id: str) -> KafkaSettings:
        return KafkaSettings(
            bootstrap_servers=bootstrap,
            schema_registry_url=schema_registry_url,
            client_id=client_id,
            group_id=group_id,
        )

    return factory


async def run_until_cancelled(listener: SignalsListener) -> None:
    try:
        await listener.start()
    except asyncio.CancelledError:
        listener.stop()
        raise
