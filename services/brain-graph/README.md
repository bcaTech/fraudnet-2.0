# brain-graph

Graph intelligence engine — community detection, motif detection, and ring
identification over the production Memgraph subgraph.

## Outputs

- **`motifs.detected.v1`** — `MotifDetectedV1` per motif match.
- **REST `POST /analyze`** — synchronous full batch (subgraph extract → motifs →
  communities → rings) with a JSON summary in the response.
- **REST `POST /scheduler/trigger`** — fires the next scheduled tick now.

The scheduled batch runs every `BRAIN_GRAPH_BATCH_INTERVAL_S` seconds (15 min
in production per CLAUDE.md §5.3).

## Motifs

| Motif | Description |
|---|---|
| `voice_sms_momo_24h` | Caller→callee voice + SMS within 1h, then callee's wallet sends within 24h. The fingerprint pattern. |
| `mule_chain` | Linear wallet→…→wallet fund flow ≥ 3 hops in time order. |
| `sim_carousel` | One device used by ≥ 3 distinct numbers (SIM-swap signature). |
| `bust_out` | Dormant wallet (≤ 3 txns / 30d) suddenly active with ≥ 5 cash-outs / 24h totalling ≥ GHS 1,000. |

## Settings

| Env | Default | Notes |
|---|---|---|
| `BRAIN_GRAPH_WINDOW_HOURS` | 24 | Subgraph extract window (most recent). |
| `BRAIN_GRAPH_MAX_NODES` | 5000 | Cap on extracted nodes per batch. |
| `BRAIN_GRAPH_BATCH_INTERVAL_S` | 900 | Scheduler cadence. |
