# stream-features

Window-aggregates voice / SMS / MoMo events into the Aerospike feature store.

Phase 1 ships as a standalone Python consumer pod (one consumer per source topic, shared `FeaturePipeline`, Aerospike sink). Phase 2 promotes to PyFlink on the Flink Kubernetes Operator without touching `pipeline.py` (per [DECISIONS.md](../../DECISIONS.md) D-002).

## What it computes

Per `Number` (msisdn):

- `velocity_1m / velocity_5m / velocity_1h` — calls in window
- `fanout_1h` — unique callees in last hour
- `imei_count` — distinct IMEIs in last 30 d
- `sms_freq_1h` — SMS sent in last hour
- `sms_template_top` — most frequent template hash (1 h)

Per `Wallet`:

- `txn_velocity_1h` — transactions in last hour
- `counterparty_diversity_24h` — unique counterparties in 24 h
- `value_p95_24h` — 95th-percentile transaction amount in 24 h

## Watermarking

Event-time semantics with a **30 s** lateness allowance per CLAUDE.md §12. Late events are dropped and counted in the `late_events_dropped` counter.

## Local dev

```bash
make infra-up && make kafka-topics-create
make dev SERVICE=stream-features
```

## Operational

- **Inputs:** `voice.events.v1`, `sms.events.v1`, `momo.events.v1`
- **Output:** Aerospike `fraudnet` namespace, sets `numbers` / `wallets`
- **Group id:** shared across replicas; partitions balance automatically
- **TTL:** 24 h on every record (matches longest window)

## Endpoints

| Path | Purpose |
|---|---|
| `GET /health/{live,ready}` | k8s probes |
| `GET /metrics` | Prometheus scrape |

## Runbook

[`docs/runbooks/stream-features.md`](../../docs/runbooks/stream-features.md)
