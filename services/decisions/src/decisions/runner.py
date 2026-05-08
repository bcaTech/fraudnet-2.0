"""decisions runner.

Two consumers (signals + motifs) each evaluate against the policy, run the
suppression check, and dispatch via the DecisionDispatcher.
"""

from __future__ import annotations

import asyncio

from fraudnet.kafka import AvroConsumer, DLQRouter, KafkaSettings
from fraudnet.kafka.consumer import ConsumedMessage
from fraudnet.obs import counter, get_logger
from fraudnet.schemas.events import MotifDetectedV1
from fraudnet.schemas.signals import SignalEventV1
from decisions.dispatcher import DecisionDispatcher
from decisions.policy import Policy, evaluate_motif, evaluate_signal
from decisions.suppression import SuppressionStore, record_suppressed

_log = get_logger("decisions.runner")

_EVALUATED = counter(
    "decisions_evaluated_total",
    "Signals/motifs evaluated.",
    labelnames=("source", "rule_id"),
)


class DecisionRunner:
    def __init__(
        self,
        *,
        policy: Policy,
        suppression: SuppressionStore,
        dispatcher: DecisionDispatcher,
        kafka_settings_factory,
    ) -> None:
        self._policy = policy
        self._supp = suppression
        self._dispatcher = dispatcher
        self._make_settings = kafka_settings_factory
        self._stop = asyncio.Event()
        self._consumers: list[object] = []

    async def start(self) -> None:
        signals = AvroConsumer(
            settings=self._make_settings("decisions-signals"),
            topic="fraud.signals.v1",
            model_cls=SignalEventV1,
            dlq=DLQRouter(self._make_settings("decisions-dlq")),
        )
        motifs = AvroConsumer(
            settings=self._make_settings("decisions-motifs"),
            topic="motifs.detected.v1",
            model_cls=MotifDetectedV1,
            dlq=DLQRouter(self._make_settings("decisions-dlq")),
        )
        self._consumers = [signals, motifs]

        async with asyncio.TaskGroup() as tg:
            tg.create_task(signals.run(self._on_signal), name="consume-signals")
            tg.create_task(motifs.run(self._on_motif), name="consume-motifs")
            tg.create_task(self._stop.wait(), name="stop")

    async def stop(self) -> None:
        self._stop.set()
        for c in self._consumers:
            c.stop()  # type: ignore[attr-defined]
        await self._supp.close()

    async def _on_signal(self, msg: ConsumedMessage[SignalEventV1]) -> None:
        sig = msg.payload
        outcome = evaluate_signal(self._policy, sig)
        _EVALUATED.labels(source="signal", rule_id=outcome.rule_id).inc()

        # Suppression: only check if window > 0 and we have a suppression key.
        if outcome.suppression_window_s > 0 and sig.suppression_key:
            ok = await self._supp.claim(
                f"{sig.suppression_key}:{outcome.action}",
                ttl_s=outcome.suppression_window_s,
            )
            if not ok:
                record_suppressed(tier=outcome.tier.value, action=outcome.action)
                return

        await self._dispatcher.dispatch_signal(sig, outcome)

    async def _on_motif(self, msg: ConsumedMessage[MotifDetectedV1]) -> None:
        m = msg.payload
        outcome = evaluate_motif(self._policy, m)
        _EVALUATED.labels(source="motif", rule_id=outcome.rule_id).inc()

        if outcome.suppression_window_s > 0 and m.members:
            sk = f"{m.tenant_id}:motif:{m.motif}:{m.members[0].id}:{outcome.action}"
            ok = await self._supp.claim(sk, ttl_s=outcome.suppression_window_s)
            if not ok:
                record_suppressed(tier=outcome.tier.value, action=outcome.action)
                return

        await self._dispatcher.dispatch_motif(m, outcome)


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
