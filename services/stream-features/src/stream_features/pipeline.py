"""Stateful windowing logic.

Pure functions over event streams; no Kafka or Aerospike imports. This is
what tests assert on, and it's the same logic that the Phase-2 PyFlink job
will wrap in `pyflink_job.py`.

Windows are sliding/tumbling fixed-size buckets keyed on entity id. We retain
event timestamps for the longest window we need (1h for voice / sms,
24h for MoMo counterparty diversity) and prune older entries on each event.

Watermarking: events older than `watermark_lateness_ms` past the highest
seen event time on a key are silently dropped (and counted in a metric).
Per CLAUDE.md §12 watermarks are sacred — drift here corrupts everything
downstream.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Iterable

from fraudnet.features.snapshot import NumberFeatures, WalletFeatures
from fraudnet.schemas.events import (
    MoMoEventType,
    MoMoEventV1,
    SmsEventV1,
    VoiceEventV1,
)

# Window sizes in milliseconds.
_W1M = 60 * 1000
_W5M = 5 * 60 * 1000
_W1H = 60 * 60 * 1000
_W24H = 24 * 60 * 60 * 1000

# Default watermark lateness — events older than this past the high watermark
# on a key are considered late and dropped.
DEFAULT_LATENESS_MS = 30_000


@dataclass
class _NumberState:
    """Per-MSISDN sliding-window state."""

    high_ts_ms: int = 0
    call_times: deque[int] = field(default_factory=deque)             # event_ts_ms
    callees_1h: deque[tuple[int, str]] = field(default_factory=deque)  # (ts, callee)
    sms_times: deque[int] = field(default_factory=deque)
    sms_template_hashes: deque[tuple[int, str]] = field(default_factory=deque)
    imeis_30d: dict[str, int] = field(default_factory=dict)            # imei → last_seen_ts


@dataclass
class _WalletState:
    high_ts_ms: int = 0
    txn_times: deque[int] = field(default_factory=deque)
    counterparties_24h: deque[tuple[int, str]] = field(default_factory=deque)
    txn_amounts_24h: deque[tuple[int, int]] = field(default_factory=deque)


class FeaturePipeline:
    """In-memory accumulator. Per-key state, bounded by retention windows.

    Tests treat it as a deterministic function: feed events in event-time
    order, read computed features back. Production wires this behind a
    Kafka consumer.
    """

    def __init__(self, *, watermark_lateness_ms: int = DEFAULT_LATENESS_MS) -> None:
        self._numbers: dict[str, _NumberState] = {}
        self._wallets: dict[str, _WalletState] = {}
        self._lateness = watermark_lateness_ms
        self.late_events_dropped = 0

    # ------------------------------------------------------------------
    # Voice
    # ------------------------------------------------------------------
    def feed_voice(self, ev: VoiceEventV1) -> NumberFeatures:
        ns = self._numbers.setdefault(ev.caller, _NumberState())
        if self._is_late(ns.high_ts_ms, ev.event_ts_ms):
            self.late_events_dropped += 1
            return self.number_features(ev.caller)
        ns.high_ts_ms = max(ns.high_ts_ms, ev.event_ts_ms)

        if ev.kind == "call_start" and ev.callee:
            ns.call_times.append(ev.event_ts_ms)
            ns.callees_1h.append((ev.event_ts_ms, ev.callee))
        if ev.imei:
            ns.imeis_30d[ev.imei] = ev.event_ts_ms

        self._prune_number(ns, ev.event_ts_ms)
        return self._compute_number_features(ev.caller, ns, ev.event_ts_ms)

    # ------------------------------------------------------------------
    # SMS
    # ------------------------------------------------------------------
    def feed_sms(self, ev: SmsEventV1) -> NumberFeatures:
        ns = self._numbers.setdefault(ev.sender, _NumberState())
        if self._is_late(ns.high_ts_ms, ev.event_ts_ms):
            self.late_events_dropped += 1
            return self.number_features(ev.sender)
        ns.high_ts_ms = max(ns.high_ts_ms, ev.event_ts_ms)

        ns.sms_times.append(ev.event_ts_ms)
        if ev.template_hash:
            ns.sms_template_hashes.append((ev.event_ts_ms, ev.template_hash))

        self._prune_number(ns, ev.event_ts_ms)
        return self._compute_number_features(ev.sender, ns, ev.event_ts_ms)

    # ------------------------------------------------------------------
    # MoMo
    # ------------------------------------------------------------------
    def feed_momo(self, ev: MoMoEventV1) -> WalletFeatures | None:
        # Outbound transactions update the sender wallet's velocity / diversity
        # state. Inbound-only events (cash_in from agent) update the recipient.
        wallet_id = ev.sender_wallet_id or ev.recipient_wallet_id
        if not wallet_id:
            return None

        ws = self._wallets.setdefault(wallet_id, _WalletState())
        if self._is_late(ws.high_ts_ms, ev.event_ts_ms):
            self.late_events_dropped += 1
            return self.wallet_features(wallet_id)
        ws.high_ts_ms = max(ws.high_ts_ms, ev.event_ts_ms)

        # Reversals don't add to velocity; they invalidate a previous send.
        # Phase 1 simplification: count reversals as zero-effect on velocity
        # but retain in the diversity counter so the topology signal stays.
        if ev.kind != MoMoEventType.REVERSAL:
            ws.txn_times.append(ev.event_ts_ms)
            ws.txn_amounts_24h.append((ev.event_ts_ms, ev.amount_minor))

        counterparty = ev.recipient_wallet_id or ev.counterparty_account_hash
        if counterparty:
            ws.counterparties_24h.append((ev.event_ts_ms, counterparty))

        self._prune_wallet(ws, ev.event_ts_ms)
        return self._compute_wallet_features(wallet_id, ws, ev.event_ts_ms)

    # ------------------------------------------------------------------
    # Read-only views
    # ------------------------------------------------------------------
    def number_features(self, msisdn: str) -> NumberFeatures:
        ns = self._numbers.get(msisdn)
        if ns is None:
            return NumberFeatures(msisdn=msisdn)
        return self._compute_number_features(msisdn, ns, ns.high_ts_ms)

    def wallet_features(self, wallet_id: str) -> WalletFeatures:
        ws = self._wallets.get(wallet_id)
        if ws is None:
            return WalletFeatures(wallet_id=wallet_id)
        return self._compute_wallet_features(wallet_id, ws, ws.high_ts_ms)

    def known_numbers(self) -> Iterable[str]:
        return self._numbers.keys()

    def known_wallets(self) -> Iterable[str]:
        return self._wallets.keys()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _is_late(self, high_ts_ms: int, event_ts_ms: int) -> bool:
        return high_ts_ms > 0 and (high_ts_ms - event_ts_ms) > self._lateness

    def _prune_number(self, ns: _NumberState, now_ms: int) -> None:
        # Drop entries older than the longest window we use (30d for IMEI,
        # 1h for fanout/calls/sms). IMEIs are dict-pruned at compute time.
        h_cut = now_ms - _W1H
        while ns.call_times and ns.call_times[0] < (now_ms - _W1H):
            ns.call_times.popleft()
        while ns.callees_1h and ns.callees_1h[0][0] < h_cut:
            ns.callees_1h.popleft()
        while ns.sms_times and ns.sms_times[0] < (now_ms - _W1H):
            ns.sms_times.popleft()
        while ns.sms_template_hashes and ns.sms_template_hashes[0][0] < h_cut:
            ns.sms_template_hashes.popleft()
        # IMEIs use a 30-day lookback — we keep the dict bounded.
        cutoff = now_ms - 30 * 24 * 60 * 60 * 1000
        for imei in [k for k, ts in ns.imeis_30d.items() if ts < cutoff]:
            del ns.imeis_30d[imei]

    def _prune_wallet(self, ws: _WalletState, now_ms: int) -> None:
        cut_24h = now_ms - _W24H
        while ws.txn_times and ws.txn_times[0] < (now_ms - _W1H):
            ws.txn_times.popleft()
        while ws.counterparties_24h and ws.counterparties_24h[0][0] < cut_24h:
            ws.counterparties_24h.popleft()
        while ws.txn_amounts_24h and ws.txn_amounts_24h[0][0] < cut_24h:
            ws.txn_amounts_24h.popleft()

    def _compute_number_features(
        self, msisdn: str, ns: _NumberState, now_ms: int
    ) -> NumberFeatures:
        v1m = sum(1 for t in ns.call_times if t > now_ms - _W1M)
        v5m = sum(1 for t in ns.call_times if t > now_ms - _W5M)
        v1h = len(ns.call_times)
        fanout = len({c for ts, c in ns.callees_1h if ts > now_ms - _W1H})
        sms_1h = len(ns.sms_times)
        # Top template by frequency
        template_counts: dict[str, int] = {}
        for _, h in ns.sms_template_hashes:
            template_counts[h] = template_counts.get(h, 0) + 1
        top = max(template_counts.items(), key=lambda kv: kv[1])[0] if template_counts else None
        return NumberFeatures(
            msisdn=msisdn,
            velocity_1m=v1m,
            velocity_5m=v5m,
            velocity_1h=v1h,
            fanout_1h=fanout,
            imei_count=len(ns.imeis_30d),
            geo_entropy=0.0,  # placeholder — computed when cell_id stream lands
            sms_freq_1h=sms_1h,
            sms_template_top=top,
        )

    def _compute_wallet_features(
        self, wallet_id: str, ws: _WalletState, now_ms: int
    ) -> WalletFeatures:
        txn_1h = len(ws.txn_times)
        cp_24h = len({c for _, c in ws.counterparties_24h})
        amounts = sorted(a for _, a in ws.txn_amounts_24h)
        if amounts:
            idx = max(0, int(len(amounts) * 0.95) - 1)
            p95 = float(amounts[idx])
        else:
            p95 = 0.0
        return WalletFeatures(
            wallet_id=wallet_id,
            txn_velocity_1h=txn_1h,
            counterparty_diversity_24h=cp_24h,
            value_p95_24h=p95,
        )
