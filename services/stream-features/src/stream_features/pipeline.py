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
    DataEventV1,
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
# Baseline (typical-traffic) window for the data-volume anomaly signal.
# 7d gives a stable per-subscriber baseline that absorbs day-of-week
# variation; we use a rolling mean rather than a true distribution to
# keep state small (per-subscriber sum + count is enough).
_W7D = 7 * 24 * 60 * 60 * 1000

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
    # Phase 3 — data signals.
    dns_query_times: deque[int] = field(default_factory=deque)
    suspicious_dns_times: deque[int] = field(default_factory=deque)
    data_volume_1h: deque[tuple[int, int]] = field(default_factory=deque)   # (ts, bytes)
    # Rolling 7d baseline kept as (ts, bytes) so we can prune cheaply.
    # We compute the baseline as mean-bytes-per-hour to compare against
    # data_volume_1h_bytes; bias is acceptable for the anomaly signal.
    data_volume_baseline: deque[tuple[int, int]] = field(default_factory=deque)


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

    def __init__(
        self,
        *,
        watermark_lateness_ms: int = DEFAULT_LATENESS_MS,
        suspicious_domains: set[str] | None = None,
    ) -> None:
        self._numbers: dict[str, _NumberState] = {}
        self._wallets: dict[str, _WalletState] = {}
        self._lateness = watermark_lateness_ms
        # The suspicious domain set is hot-loaded from brain-content's
        # blocklist + newly-registered list. The pipeline accepts it
        # by reference so the runner can update it in-place between
        # refreshes without rebuilding state.
        self._suspicious = suspicious_domains if suspicious_domains is not None else set()
        self.late_events_dropped = 0

    @property
    def suspicious_domains(self) -> set[str]:
        return self._suspicious

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
    # Data (DNS / IPDR) — Phase 3
    # ------------------------------------------------------------------
    def feed_data(self, ev: DataEventV1) -> NumberFeatures | None:
        """Update DNS-rate / suspicious-domain / volume features.

        Returns None for unattributed events (no MSISDN); they still inform
        domain-level reputation in stream-graph but do not move per-subscriber
        feature state.
        """
        if not ev.msisdn:
            return None
        ns = self._numbers.setdefault(ev.msisdn, _NumberState())
        if self._is_late(ns.high_ts_ms, ev.event_ts_ms):
            self.late_events_dropped += 1
            return self.number_features(ev.msisdn)
        ns.high_ts_ms = max(ns.high_ts_ms, ev.event_ts_ms)

        if ev.kind in {"dns_query", "dns_response"} and ev.domain:
            ns.dns_query_times.append(ev.event_ts_ms)
            if self._is_suspicious(ev.domain):
                ns.suspicious_dns_times.append(ev.event_ts_ms)
        elif ev.kind == "ipdr_session":
            total_bytes = (ev.bytes_up or 0) + (ev.bytes_down or 0)
            if total_bytes > 0:
                ns.data_volume_1h.append((ev.event_ts_ms, total_bytes))
                ns.data_volume_baseline.append((ev.event_ts_ms, total_bytes))

        self._prune_number(ns, ev.event_ts_ms)
        return self._compute_number_features(ev.msisdn, ns, ev.event_ts_ms)

    def _is_suspicious(self, domain: str) -> bool:
        # Membership check covers exact-match blocklist; domains land here
        # already canonicalised (lowercase, A-label) by the ingest adapter.
        if domain in self._suspicious:
            return True
        # Suffix match — eTLD+1 entries match all subdomains. Cheap because
        # most domains are short (label count < 10).
        labels = domain.split(".")
        for i in range(1, len(labels)):
            if ".".join(labels[i:]) in self._suspicious:
                return True
        return False

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
        # 1h for fanout/calls/sms, 7d for data baseline). IMEIs are
        # dict-pruned at compute time.
        h_cut = now_ms - _W1H
        while ns.call_times and ns.call_times[0] < (now_ms - _W1H):
            ns.call_times.popleft()
        while ns.callees_1h and ns.callees_1h[0][0] < h_cut:
            ns.callees_1h.popleft()
        while ns.sms_times and ns.sms_times[0] < (now_ms - _W1H):
            ns.sms_times.popleft()
        while ns.sms_template_hashes and ns.sms_template_hashes[0][0] < h_cut:
            ns.sms_template_hashes.popleft()
        while ns.dns_query_times and ns.dns_query_times[0] < h_cut:
            ns.dns_query_times.popleft()
        while ns.suspicious_dns_times and ns.suspicious_dns_times[0] < h_cut:
            ns.suspicious_dns_times.popleft()
        while ns.data_volume_1h and ns.data_volume_1h[0][0] < h_cut:
            ns.data_volume_1h.popleft()
        baseline_cut = now_ms - _W7D
        while ns.data_volume_baseline and ns.data_volume_baseline[0][0] < baseline_cut:
            ns.data_volume_baseline.popleft()
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
        dns_qrate_1h = len(ns.dns_query_times)
        susp_dom_1h = len(ns.suspicious_dns_times)
        dvol_1h = sum(b for _, b in ns.data_volume_1h)
        # Baseline is mean per-hour bytes over the 7d window. We approximate
        # by dividing the 7d sum by 168 hours; underweights cold-start
        # subscribers but converges quickly.
        if ns.data_volume_baseline:
            base_total = sum(b for _, b in ns.data_volume_baseline)
            dvol_base = base_total // 168
        else:
            dvol_base = 0
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
            dns_query_rate_1h=dns_qrate_1h,
            suspicious_domain_count_1h=susp_dom_1h,
            data_volume_1h_bytes=dvol_1h,
            data_volume_baseline_bytes=dvol_base,
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
