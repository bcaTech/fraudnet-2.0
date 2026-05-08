# stream-graph

Per-event Memgraph mutations + emission of `graph.mutations.v1` control events. **Not** windowed (CLAUDE.md §12); features are window-aggregated, the graph is per-event.

Phase 1 ships as a standalone Python consumer pod with the pipeline logic isolated in `pipeline.py`. Phase 2 ports to PyFlink (DECISIONS.md D-002).

## What it does per event

| Source | Memgraph mutations | Notes |
|---|---|---|
| `voice.events.v1 / call_start` | `MERGE (:Number)` for caller and callee, `CREATE (:Number)-[:CALLED]->(:Number)`, `MERGE (:Device)` + `(:Number)-[:USED]->(:Device)` if IMEI present | |
| `voice.events.v1 / call_end` | nodes only (call edge already created at start) | |
| `sms.events.v1 / mt|mo` | `MERGE (:Number)` for sender/recipient, `CREATE (:Number)-[:SMSED]->(:Number)` with `template_hash` property | DR events do not create edges |
| `momo.events.v1` | `MERGE (:Wallet)` for sender/recipient, `(:Number)-[:OWNS]->(:Wallet)` if msisdn known, `(:Wallet)-[:SENT]->(:Wallet)` for transfers | reversal/cash_in skip the SENT edge; bank cash-out creates `(:Account)` + `[:CASHED_OUT_TO]` |

Every mutation also emits a `GraphMutationV1` to `graph.mutations.v1` so subscribers see fresh control events even before the buffered Memgraph writer flushes.

## Endpoints

| Path | Purpose |
|---|---|
| `GET /health/{live,ready}` | k8s probes |
| `GET /metrics` | Prometheus scrape |

## Operational

- Memgraph writes go through `BufferedGraphWriter` (default 1000-mutation buffer, 5 s flush) — never per-mutation on the hot path.
- Memgraph is in-memory: restart loses transient state. Recovery via Kafka replay + lakehouse (`tools/replay`).
- Snapshots every 6 h.

## Runbook

[`docs/runbooks/stream-graph.md`](../../docs/runbooks/stream-graph.md)
