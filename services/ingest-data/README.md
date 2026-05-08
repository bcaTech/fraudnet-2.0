# ingest-data

DNS resolver + IPDR ingestion. Two webhooks, one canonical event shape, one Kafka topic (`data.events.v1`).

| Endpoint | Source | Purpose |
|---|---|---|
| `POST /dns/push` | DNS resolver | Per-query / per-response events; subscriber attribution best-effort |
| `POST /ipdr/push` | IPDR collector | Per-session detail records with subscriber MSISDN + bytes counters |
| `GET  /health/{live,ready}` | infra | Liveness + readiness |
| `GET  /metrics` | infra | Prometheus exposition |

See `CLAUDE.md` §5.1 (ingestion patterns) and `docs/data-contracts/dns-push.md` / `docs/data-contracts/ipdr-push.md` for the wire format. Service spec runbook: `docs/runbooks/ingest-data.md`.

## Why two endpoints share one topic

DNS and IPDR carry the same fraud-relevant signal — *which subscriber talked to which destination, when, and how much* — at different fidelities. Stream-features and stream-graph reason over the canonical `DataEventV1`; whether it came from a resolver log or a DPI session is left in the `source` and `kind` fields. Splitting topics would force every downstream consumer to do the union join we already perform here.
