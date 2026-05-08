"""stream-graph runner.

Three Avro consumers (voice/sms/momo) → per-event translator → two parallel
sinks: BufferedGraphWriter to Memgraph and AvroProducer for graph.mutations.v1.

Both sinks are async. Memgraph writes go through the buffered writer so we
honour the §5.2 "never enable individual writes on the hot path" rule. The
graph.mutations.v1 emission is per-mutation so other services see fresh
control events even if the Memgraph buffer hasn't flushed yet.
"""

from __future__ import annotations

import asyncio
from typing import Iterable

from fraudnet.graph import BufferedGraphWriter, GraphClient, GraphScope
from fraudnet.graph.batch_writer import GraphMutation
from fraudnet.kafka import AvroConsumer, AvroProducer, DLQRouter, KafkaSettings
from fraudnet.kafka.consumer import ConsumedMessage
from fraudnet.obs import counter, get_logger
from fraudnet.schemas.events import (
    GraphMutationV1,
    MoMoEventV1,
    SmsEventV1,
    VoiceEventV1,
)
from stream_graph.pipeline import GraphOp, translate_momo, translate_sms, translate_voice

_log = get_logger("stream_graph.runner")

_TRANSLATED = counter(
    "stream_graph_mutations_translated_total",
    "Graph mutations produced by the per-event translator.",
    labelnames=("source_topic", "op"),
)


class GraphRunner:
    def __init__(
        self,
        *,
        graph_client: GraphClient,
        scope: GraphScope,
        producer: AvroProducer[GraphMutationV1],
        kafka_settings_factory,
        graph_buffer_max: int,
        graph_flush_interval_s: float,
    ) -> None:
        self._client = graph_client
        self._scope = scope
        self._producer = producer
        self._make_settings = kafka_settings_factory
        self._writer = BufferedGraphWriter(
            client=graph_client,
            scope=scope,
            max_buffer=graph_buffer_max,
            flush_interval_s=graph_flush_interval_s,
        )
        self._consumers: list[object] = []
        self._stop = asyncio.Event()

    async def start(self) -> None:
        await self._writer.start()

        voice = AvroConsumer(
            settings=self._make_settings("stream-graph-voice"),
            topic="voice.events.v1",
            model_cls=VoiceEventV1,
            dlq=DLQRouter(self._make_settings("stream-graph-dlq")),
        )
        sms = AvroConsumer(
            settings=self._make_settings("stream-graph-sms"),
            topic="sms.events.v1",
            model_cls=SmsEventV1,
            dlq=DLQRouter(self._make_settings("stream-graph-dlq")),
        )
        momo = AvroConsumer(
            settings=self._make_settings("stream-graph-momo"),
            topic="momo.events.v1",
            model_cls=MoMoEventV1,
            dlq=DLQRouter(self._make_settings("stream-graph-dlq")),
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
            c.stop()  # type: ignore[attr-defined]
        await self._writer.stop()
        await self._producer.stop()
        await self._client.close()

    # ------------------------------------------------------------------
    # Per-topic handlers
    # ------------------------------------------------------------------
    async def _on_voice(self, msg: ConsumedMessage[VoiceEventV1]) -> None:
        await self._dispatch(
            translate_voice(msg.payload),
            event_id=msg.payload.event_id,
            event_ts_ms=msg.payload.event_ts_ms,
            ingest_ts_ms=msg.payload.ingest_ts_ms,
            source_topic="voice.events.v1",
        )

    async def _on_sms(self, msg: ConsumedMessage[SmsEventV1]) -> None:
        await self._dispatch(
            translate_sms(msg.payload),
            event_id=msg.payload.event_id,
            event_ts_ms=msg.payload.event_ts_ms,
            ingest_ts_ms=msg.payload.ingest_ts_ms,
            source_topic="sms.events.v1",
        )

    async def _on_momo(self, msg: ConsumedMessage[MoMoEventV1]) -> None:
        await self._dispatch(
            translate_momo(msg.payload),
            event_id=msg.payload.event_id,
            event_ts_ms=msg.payload.event_ts_ms,
            ingest_ts_ms=msg.payload.ingest_ts_ms,
            source_topic="momo.events.v1",
        )

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    async def _dispatch(
        self,
        ops: Iterable[GraphOp],
        *,
        event_id: str,
        event_ts_ms: int,
        ingest_ts_ms: int,
        source_topic: str,
    ) -> None:
        for op in ops:
            _TRANSLATED.labels(source_topic=source_topic, op=op.op).inc()

            # Memgraph batch write
            await self._writer.append(
                GraphMutation(
                    op=op.op,
                    payload={
                        "id": op.node_id or "",
                        "src": op.src_id or "",
                        "dst": op.dst_id or "",
                        "properties": dict(op.properties),
                    },
                )
            )

            # Control-topic emission. Per-mutation so subscribers don't wait
            # for the buffered writer's flush cadence.
            mutation = op.to_mutation(
                event_id=event_id,
                event_ts_ms=event_ts_ms,
                ingest_ts_ms=ingest_ts_ms,
                source=f"stream-graph:{source_topic}",
            )
            # Partition key: prefer node_id; fall back to src_id for edges so
            # a node's mutations co-locate.
            key = mutation.node_id or mutation.src_id or event_id
            await self._producer.send(mutation, key=key)


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
