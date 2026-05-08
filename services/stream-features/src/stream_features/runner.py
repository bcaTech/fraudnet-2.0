"""Phase-1 standalone runner: Kafka consumers → FeaturePipeline → Aerospike.

For each input topic we run a consumer in a TaskGroup, all writing to one
shared FeaturePipeline. Aerospike writes are flushed periodically per-key
when the pipeline reports new feature snapshots.

This is the production deployment shape for Phase 1. Phase 2 swaps this
runner for a PyFlink job (pyflink_job.py) without touching pipeline.py.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fraudnet.features import FeatureStore
from fraudnet.kafka import AvroConsumer, ConsumerHandler, DLQRouter, KafkaSettings
from fraudnet.kafka.consumer import ConsumedMessage
from fraudnet.obs import counter, get_logger
from fraudnet.schemas.events import MoMoEventV1, SmsEventV1, VoiceEventV1
from stream_features.pipeline import FeaturePipeline

_log = get_logger("stream_features.runner")

_PROCESSED = counter(
    "stream_features_events_processed_total",
    "Events processed by the feature pipeline.",
    labelnames=("topic",),
)


class FeatureRunner:
    def __init__(
        self,
        *,
        pipeline: FeaturePipeline,
        feature_store: FeatureStore,
        kafka_settings_factory,  # callable: client_id -> KafkaSettings
        feature_ttl_s: int = 86_400,
    ) -> None:
        self._pipeline = pipeline
        self._store = feature_store
        self._make_settings = kafka_settings_factory
        self._ttl = feature_ttl_s
        self._consumers: list[Any] = []
        self._stop = asyncio.Event()

    async def start(self) -> None:
        # One consumer per source topic. Group id is shared so partitions
        # are balanced across replicas of the runner pod in production.
        voice = AvroConsumer(
            settings=self._make_settings("stream-features-voice"),
            topic="voice.events.v1",
            model_cls=VoiceEventV1,
            dlq=DLQRouter(self._make_settings("stream-features-dlq")),
        )
        sms = AvroConsumer(
            settings=self._make_settings("stream-features-sms"),
            topic="sms.events.v1",
            model_cls=SmsEventV1,
            dlq=DLQRouter(self._make_settings("stream-features-dlq")),
        )
        momo = AvroConsumer(
            settings=self._make_settings("stream-features-momo"),
            topic="momo.events.v1",
            model_cls=MoMoEventV1,
            dlq=DLQRouter(self._make_settings("stream-features-dlq")),
        )
        self._consumers = [voice, sms, momo]

        async with asyncio.TaskGroup() as tg:
            tg.create_task(voice.run(self._on_voice), name="consume-voice")
            tg.create_task(sms.run(self._on_sms), name="consume-sms")
            tg.create_task(momo.run(self._on_momo), name="consume-momo")
            tg.create_task(self._stop.wait(), name="stop-signal")

    async def stop(self) -> None:
        self._stop.set()
        for c in self._consumers:
            c.stop()
        await self._store.close()

    async def _on_voice(self, msg: ConsumedMessage[VoiceEventV1]) -> None:
        nf = self._pipeline.feed_voice(msg.payload)
        await self._store.put_number(nf, ttl_s=self._ttl)
        _PROCESSED.labels(topic="voice.events.v1").inc()

    async def _on_sms(self, msg: ConsumedMessage[SmsEventV1]) -> None:
        nf = self._pipeline.feed_sms(msg.payload)
        await self._store.put_number(nf, ttl_s=self._ttl)
        _PROCESSED.labels(topic="sms.events.v1").inc()

    async def _on_momo(self, msg: ConsumedMessage[MoMoEventV1]) -> None:
        wf = self._pipeline.feed_momo(msg.payload)
        if wf is not None:
            await self._store.put_wallet(wf, ttl_s=self._ttl)
        _PROCESSED.labels(topic="momo.events.v1").inc()


def make_settings_factory(
    *,
    bootstrap: str,
    schema_registry_url: str,
    group_id: str,
):
    def factory(client_id: str) -> KafkaSettings:
        return KafkaSettings(
            bootstrap_servers=bootstrap,
            schema_registry_url=schema_registry_url,
            client_id=client_id,
            group_id=group_id,
        )

    return factory


# ConsumerHandler typing helper export (not used at runtime — just keeps the
# types module-level for downstream imports).
_ = ConsumerHandler
