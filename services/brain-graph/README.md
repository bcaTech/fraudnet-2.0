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
| `voice_then_momo_30m` | Caller→callee voice, caller's wallet sends to callee's wallet within 30 min (Phase 3). |
| `sms_url_blocklist` | SMS lure → recipient queries flagged domain within 1 h (Phase 3). |
| `device_sim_wallet_fusion` | Device shared by ≥ 2 numbers AND at least one owns an active wallet (Phase 3). |
| `cross_opco_ring` | Local ring whose fund flow exits to an identifier confirmed by a peer opco via federation lookup (Phase 4). |

## Phase 4 cross-opco

When `FEDERATION_PEERS` is configured, `analyzer.run_once()` adds a
`cross_opco` phase after the local ring identification. For every local
ring with outgoing wallet flow, the analyser hashes the external
identifier and queries each peer opco. Any peer confirmation lifts the
ring's composite score and emits a `cross_opco_ring` motif.

PII rule: the analyser hashes locally before any peer call. Plaintext
identifiers never cross opco boundaries. See
`docs/architecture/federation-protocol.md` for the wire-format details
and `cross_opco.py` for the detector.

## Settings

| Env | Default | Notes |
|---|---|---|
| `BRAIN_GRAPH_WINDOW_HOURS` | 24 | Subgraph extract window (most recent). |
| `BRAIN_GRAPH_MAX_NODES` | 5000 | Cap on extracted nodes per batch. |
| `BRAIN_GRAPH_BATCH_INTERVAL_S` | 900 | Scheduler cadence. |
| `FEDERATION_PEERS` | _empty_ | `name=url` pairs comma-separated. Empty disables Phase 4 cross-opco detection. |
| `FEDERATION_SHARED_SECRET` | _dev secret_ | Per-peer-pair HMAC secret (Vault in production). |
