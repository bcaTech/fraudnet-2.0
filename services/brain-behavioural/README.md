# brain-behavioural

Behavioural scoring service. Phase 1 ships a heuristic model behind a fixed `Scorer` interface ([DECISIONS.md](../../DECISIONS.md) D-006). Phase 2 swaps to a LightGBM artefact loaded from the model registry without touching the API surface.

## Two paths

- **Async** — subscribes to `graph.mutations.v1`. On each `:Number` or `:Wallet` upsert, fetches the latest feature snapshot from Aerospike, scores, and publishes a `SignalEventV1` to `fraud.signals.v1` if a `signal_kind` triggers.
- **Sync** — `POST /score/number` and `POST /score/wallet` REST endpoints for ad-hoc scoring.

## Phase 1 heuristic rules

| Rule | Triggers | Score | Severity |
|---|---|---|---|
| Voice velocity burst | `velocity_1m ≥ 10` and `fanout_1h ≥ 50` | 0.92 | high |
| IMEI churn | `imei_count ≥ 4` over 30 d | 0.78 | medium |
| SMS bulk template | `sms_freq_1h ≥ 30` with a top template hash | 0.85 | high |
| Mule velocity | `txn_velocity_1h ≥ 15` and `counterparty_diversity_24h ≥ 8` | 0.90 | high |
| High value velocity | `value_p95_24h ≥ 100k` and `txn_velocity_1h ≥ 8` | 0.82 | medium |

Sub-threshold subjects emit a graded score (≤ 0.5) with `signal_kind=None`; no signal is published.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/score/number` | Sync score `{msisdn}` → `ScoreResponse` |
| `POST` | `/score/wallet` | Sync score `{wallet_id}` → `ScoreResponse` |
| `GET`  | `/health/{live,ready}` | k8s probes |
| `GET`  | `/metrics` | Prometheus scrape |

## Operational

- **Inputs:** `graph.mutations.v1`, Aerospike feature snapshots
- **Output:** `fraud.signals.v1` (keyed by subject id)
- **Suppression key:** `<tenant>:<subject_kind>:<subject_id>:<signal_kind>` — decisions dedups on this

## Runbook

[`docs/runbooks/brain-behavioural.md`](../../docs/runbooks/brain-behavioural.md)
