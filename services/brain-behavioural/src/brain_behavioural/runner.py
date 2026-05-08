"""Async scoring runner.

Subscribes to graph.mutations.v1. For each Number / Wallet upsert event,
re-scores the subject from its Aerospike feature snapshot. Emits
SignalEventV1 to fraud.signals.v1 when a signal_kind triggers.

Why graph.mutations.v1 and not the source topics: stream-graph has already
done the per-event work to identify the affected subjects. Subscribing here
means we score on the fan-out the graph has computed, not on raw events.
"""

from __future__ import annotations

import asyncio

from fraudnet.features import FeatureStore
from fraudnet.kafka import AvroConsumer, AvroProducer, DLQRouter, KafkaSettings
from fraudnet.kafka.consumer import ConsumedMessage
from fraudnet.obs import counter, get_logger
from fraudnet.schemas.events import GraphMutationV1
from fraudnet.schemas.signals import SignalEventV1
from fraudnet.schemas.types import EntityKind
from brain_behavioural.scorer import Scorer, to_signal

_log = get_logger("brain_behavioural.runner")

_SCORED = counter(
    "brain_behavioural_scored_total",
    "Subjects scored.",
    labelnames=("entity_kind", "fired"),
)
_NOT_FOUND = counter(
    "brain_behavioural_features_missing_total",
    "Subjects scored without a feature snapshot in Aerospike.",
    labelnames=("entity_kind",),
)


class BehaviouralRunner:
    def __init__(
        self,
        *,
        scorer: Scorer,
        feature_store: FeatureStore,
        signal_producer: AvroProducer[SignalEventV1],
        kafka_settings_factory,
    ) -> None:
        self._scorer = scorer
        self._store = feature_store
        self._producer = signal_producer
        self._make_settings = kafka_settings_factory
        self._stop = asyncio.Event()
        self._consumer: object | None = None

    async def start(self) -> None:
        consumer = AvroConsumer(
            settings=self._make_settings("brain-behavioural-graph"),
            topic="graph.mutations.v1",
            model_cls=GraphMutationV1,
            dlq=DLQRouter(self._make_settings("brain-behavioural-dlq")),
        )
        self._consumer = consumer
        await consumer.run(self._on_mutation)

    async def stop(self) -> None:
        self._stop.set()
        if self._consumer is not None:
            self._consumer.stop()  # type: ignore[attr-defined]
        await self._producer.stop()
        await self._store.close()

    async def _on_mutation(self, msg: ConsumedMessage[GraphMutationV1]) -> None:
        m = msg.payload
        # We score on node upserts only — edge upserts represent already-known
        # entities and we'd score them on the source node mutation anyway.
        if m.op != "upsert_node":
            return
        if m.node_kind == "Number" and m.node_id:
            await self._score_number(m.node_id, source=m.source)
        elif m.node_kind == "Wallet" and m.node_id:
            await self._score_wallet(m.node_id, source=m.source)

    async def _score_number(self, msisdn: str, *, source: str) -> None:
        features = await self._store.get_number(msisdn)
        if features is None:
            _NOT_FOUND.labels(entity_kind="number").inc()
            return
        result = self._scorer.score_number(features)
        signal = to_signal(
            result=result,
            subject_kind=EntityKind.NUMBER,
            subject_id=msisdn,
            source=f"brain-behavioural:{source}",
        )
        fired = signal is not None
        _SCORED.labels(entity_kind="number", fired=str(fired).lower()).inc()
        if signal is not None:
            await self._producer.send(signal, key=msisdn)

    async def _score_wallet(self, wallet_id: str, *, source: str) -> None:
        features = await self._store.get_wallet(wallet_id)
        if features is None:
            _NOT_FOUND.labels(entity_kind="wallet").inc()
            return
        result = self._scorer.score_wallet(features)
        signal = to_signal(
            result=result,
            subject_kind=EntityKind.WALLET,
            subject_id=wallet_id,
            source=f"brain-behavioural:{source}",
        )
        fired = signal is not None
        _SCORED.labels(entity_kind="wallet", fired=str(fired).lower()).inc()
        if signal is not None:
            await self._producer.send(signal, key=wallet_id)


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
