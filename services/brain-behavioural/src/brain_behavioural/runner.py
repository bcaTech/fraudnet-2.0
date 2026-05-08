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

from business_registry.client import BusinessRegistryClient, NoopBusinessRegistryClient
from fraudnet.features import FeatureStore
from fraudnet.kafka import AvroConsumer, AvroProducer, DLQRouter, KafkaSettings
from fraudnet.kafka.consumer import ConsumedMessage
from fraudnet.obs import counter, get_logger
from fraudnet.schemas.events import GraphMutationV1
from fraudnet.schemas.signals import SignalEventV1
from fraudnet.schemas.types import EntityKind, RiskScore, Severity
from brain_behavioural.scorer import Scorer, ScoringResult, to_signal

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
_VERIFIED_DISCOUNT = counter(
    "brain_behavioural_verified_discount_total",
    "Score discounts applied for verified businesses.",
    labelnames=("entity_kind",),
)


VERIFIED_BUSINESS_DISCOUNT = 0.1  # multiply score by this when verified
# Signal kinds that are exempt for verified businesses (legitimate
# velocity / IMEI churn / bulk SMS).
_VERIFIED_EXEMPT_SIGNALS = frozenset(
    {
        "voice.velocity_burst",
        "device.imei_churn",
        "sms.bulk_template",
    }
)


class BehaviouralRunner:
    def __init__(
        self,
        *,
        scorer: Scorer,
        feature_store: FeatureStore,
        signal_producer: AvroProducer[SignalEventV1],
        kafka_settings_factory,
        business_registry: BusinessRegistryClient | None = None,
    ) -> None:
        self._scorer = scorer
        self._store = feature_store
        self._producer = signal_producer
        self._make_settings = kafka_settings_factory
        self._registry = business_registry or NoopBusinessRegistryClient()
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
        await self._registry.aclose()

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
        result = await self._apply_verified_discount(result, msisdn, "number")
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

    async def _apply_verified_discount(
        self, result: ScoringResult, msisdn: str, entity_kind: str
    ) -> ScoringResult:
        """If the MSISDN is a verified business, suppress the signal_kind
        and discount the score so it never triggers a customer-facing
        action. This is the registry-side defence against false positives
        on legitimate bulk senders (telcos, banks, MNOs, OTPs).
        """
        try:
            lookup = await self._registry.lookup_msisdn(msisdn)
        except Exception:  # noqa: BLE001 — registry must not break scoring
            return result
        if not lookup.is_verified:
            return result
        # Only discount when the signal_kind would have been one of the
        # bulk-sender-typical signals; other unrelated signals (e.g. mule
        # patterns) should still fire even if the MSISDN happens to be
        # a verified business — the wallet flow is separate.
        if result.signal_kind not in _VERIFIED_EXEMPT_SIGNALS:
            return result
        _VERIFIED_DISCOUNT.labels(entity_kind=entity_kind).inc()
        new_value = max(0.0, min(1.0, result.score.value * VERIFIED_BUSINESS_DISCOUNT))
        new_evidence = dict(result.evidence)
        new_evidence["verified_business"] = True
        new_evidence["verified_business_id"] = lookup.business_id or ""
        new_evidence["verified_business_name"] = lookup.business_name or ""
        new_score = RiskScore(
            value=new_value,
            model_id=result.score.model_id,
            model_version=result.score.model_version,
            computed_at_ms=result.score.computed_at_ms,
            feature_attribution=result.score.feature_attribution,
        )
        return ScoringResult(
            score=new_score,
            signal_kind=None,  # suppress the signal entirely
            severity=Severity.LOW,
            evidence=new_evidence,
        )

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
