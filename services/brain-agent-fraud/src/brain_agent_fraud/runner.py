"""Async runner — consumes momo.events.v1, runs detectors per agent
event, emits signals + updates profiles.

We trigger detection on every cash_in / cash_out / p2p_transfer event
where the channel is `agent`. Per-agent state is kept in-process; the
sliding-window store trims to the longest detector window
(`AgentStateStore.max_window_s`).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fraudnet.kafka import AvroConsumer, AvroProducer, DLQRouter
from fraudnet.kafka.consumer import ConsumedMessage
from fraudnet.obs import counter, get_logger
from fraudnet.schemas.events import MoMoEventV1
from fraudnet.schemas.signals import SignalEventV1

from brain_agent_fraud.detectors import (
    CohortLookup,
    CounterpartyHistory,
    Detection,
    detect_collusion,
    detect_commission_farming,
    detect_float_manipulation,
    detect_phantom_customer,
    detect_split_txn,
)
from brain_agent_fraud.profile import ProfileStore
from brain_agent_fraud.settings import Settings
from brain_agent_fraud.signal_builder import to_signal
from brain_agent_fraud.state import AgentStateStore, AgentTxn

_log = get_logger("brain_agent_fraud.runner")
_DETECTIONS = counter(
    "brain_agent_fraud_detections_total",
    "Detections emitted by brain-agent-fraud.",
    labelnames=("signal_kind",),
)
_SCANNED = counter(
    "brain_agent_fraud_events_scanned_total",
    "MoMo events processed by brain-agent-fraud.",
    labelnames=("relevant",),
)


class _NullCounterpartyHistory(CounterpartyHistory):
    """Default — every counterparty looks new. Tests / dev override."""

    def prior_txn_count(self, counterparty_id: str) -> int:
        return 1  # treat as known so phantom_customer doesn't always fire


class _NullCohortLookup(CohortLookup):
    def cohort_for(self, agent_id: str):  # noqa: ANN201
        return None


class AgentFraudRunner:
    def __init__(
        self,
        *,
        settings: Settings,
        signal_producer: AvroProducer[SignalEventV1],
        kafka_settings_factory,
        state: AgentStateStore | None = None,
        profiles: ProfileStore | None = None,
        history: CounterpartyHistory | None = None,
        cohorts: CohortLookup | None = None,
    ) -> None:
        self._settings = settings
        self._producer = signal_producer
        self._make_settings = kafka_settings_factory
        max_window_s = max(
            settings.collusion_window_s,
            settings.commission_farming_window_s,
            settings.split_txn_window_s,
            settings.phantom_dormancy_window_s // 24,  # daily granularity for window
            24 * 3600,
        )
        self._state = state or AgentStateStore(max_window_s=max_window_s)
        self._profiles = profiles or ProfileStore()
        self._history = history or _NullCounterpartyHistory()
        self._cohorts = cohorts or _NullCohortLookup()
        self._consumer: object | None = None
        self._stop = asyncio.Event()

    @property
    def profiles(self) -> ProfileStore:
        return self._profiles

    async def start(self) -> None:
        consumer = AvroConsumer(
            settings=self._make_settings("brain-agent-fraud"),
            topic="momo.events.v1",
            model_cls=MoMoEventV1,
            dlq=DLQRouter(self._make_settings("brain-agent-fraud-dlq")),
        )
        self._consumer = consumer
        await consumer.run(self._on_event)

    async def stop(self) -> None:
        self._stop.set()
        if self._consumer is not None:
            self._consumer.stop()  # type: ignore[attr-defined]
        await self._producer.stop()

    async def _on_event(self, msg: ConsumedMessage[MoMoEventV1]) -> None:
        m = msg.payload
        # We're scoring agents, not customers. Only the agent-side events
        # matter — `channel='agent'` or `counterparty_kind='agent'`.
        agent_msisdn = self._extract_agent_msisdn(m)
        if agent_msisdn is None:
            _SCANNED.labels(relevant="false").inc()
            return
        _SCANNED.labels(relevant="true").inc()

        cp_id = m.counterparty_account_hash or m.recipient_msisdn or m.recipient_wallet_id
        txn = AgentTxn(
            txn_id=m.txn_id,
            kind=m.kind.value,
            counterparty_kind=m.counterparty_kind,
            counterparty_id=str(cp_id) if cp_id else None,
            amount_minor=m.amount_minor,
            ts_ms=m.event_ts_ms,
            channel=m.channel,
        )
        self._state.append(agent_msisdn, txn)

        await self._run_detectors(agent_msisdn=agent_msisdn, ts_ms=m.event_ts_ms)

    def _extract_agent_msisdn(self, m: MoMoEventV1) -> str | None:
        """The MSISDN behind the agent wallet involved in this event.

        Conventions:
          - For cash-in / cash-out / merchant_payment, the agent is the
            sender for cash-out / merchant pays-out, recipient for cash-in.
            We expose `channel='agent'` reliably; default to sender otherwise.
          - For p2p_transfer with `counterparty_kind='agent'`, the agent
            is on the *counterparty* side; we don't currently track that
            agent's MSISDN here (would need a wallet→msisdn lookup) so
            we skip those events for the per-agent runner.
        """
        if m.channel != "agent":
            return None
        if m.kind in ("cash_in", "merchant_payment"):
            return str(m.recipient_msisdn) if m.recipient_msisdn else None
        # cash_out, p2p_transfer initiated by the agent
        return str(m.sender_msisdn) if m.sender_msisdn else None

    async def _run_detectors(self, *, agent_msisdn: str, ts_ms: int) -> None:
        s = self._settings
        cf_view = self._state.view(
            agent_msisdn, window_s=s.commission_farming_window_s, now_ms=ts_ms
        )
        st_view = self._state.view(
            agent_msisdn, window_s=s.split_txn_window_s, now_ms=ts_ms
        )
        ph_view = cf_view  # phantom-customer reuses commission window (1h) — typical
        coll_view = self._state.view(
            agent_msisdn, window_s=s.collusion_window_s, now_ms=ts_ms
        )
        float_view = coll_view

        detections: list[Detection] = []
        for d in (
            detect_commission_farming(
                cf_view, min_pairs=s.commission_farming_min_pairs
            ),
            detect_split_txn(
                st_view,
                threshold_minor=s.split_txn_threshold_minor,
                max_piece_minor=s.split_txn_max_size_minor,
                min_pieces=s.split_txn_min_pieces,
            ),
            detect_phantom_customer(
                ph_view, history=self._history, min_phantom_count=3
            ),
            detect_collusion(
                agent_msisdn,
                coll_view,
                cohorts=self._cohorts,
                min_cohort_size=s.collusion_min_shared_agents,
            ),
            detect_float_manipulation(
                float_view,
                excess_threshold_minor=s.float_excess_threshold_minor,
                movement_pairs_min=s.float_movement_pairs_min,
            ),
        ):
            if d is not None:
                detections.append(d)

        if not detections:
            return

        for d in detections:
            _DETECTIONS.labels(signal_kind=d.signal_kind).inc()
            signal = to_signal(
                detection=d,
                agent_msisdn=agent_msisdn,
                source="brain-agent-fraud",
            )
            await self._producer.send(signal, key=agent_msisdn)

        self._profiles.update(
            agent_id=agent_msisdn,
            detections=detections,
            txn_count=len(coll_view),
            now_ms=int(time.time() * 1000),
        )
