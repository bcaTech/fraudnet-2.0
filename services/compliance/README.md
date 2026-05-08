# compliance

Audit-log writer + regulator export builder. Per CLAUDE.md §5.5 and §7.3, this service owns the WORM-style audit trail that is the single source of truth for regulator inquiries.

## Inputs

| Topic | Schema | Persisted to |
|---|---|---|
| `audit.events.v1` | `AuditEventV1` | `audit_events` (monthly partitions) |
| `decisions.dispatched.v1` | `DecisionDispatchedV1` | `decision_audits` |

Both consumers are append-only — the service has INSERT-only DB grants in production.

## Storage

Lives in a separate `fraudnet_audit` Postgres database from the operational `fraudnet` DB so retention and access policies can diverge. `audit_events` is partitioned monthly (`migrations/0001_audit.sql`); a Phase-2 cron rolls the partitions forward and archives months >6 months old to Iceberg.

## API

Read-only. No writes from the HTTP surface.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/audit/by_request/{request_id}` | Audit chain for a single request_id |
| `GET` | `/audit/range?since=&until=&tenant_id=&limit=` | Range query (≤10k rows) |
| `GET` | `/audit/export?since=&until=&tenant_id=` | NDJSON streaming export |
| `GET` | `/health/{live,ready}` | k8s probes |
| `GET` | `/metrics` | Prometheus scrape |

`audit/export` is the Phase-1 regulator-export shape; Phase 2 swaps to per-regulator templated submission packs (NCA / DPC / BoG / CSA).

## Idempotency

`audit_events.id` is derived from the event_id via `uuid5` so consumer redeliveries collapse on `ON CONFLICT (id) DO NOTHING`. `decision_audits.decision_id` plays the same role.

## Phase 2 (not in this release)

- Purpose-limitation enforcer sidecar (intercepts cross-purpose reads on PII tables).
- Iceberg archive cron with WORM retention semantics.
- Per-regulator export templates.

## Operational

- **Postgres DSN:** `AUDIT_POSTGRES_DSN` env var; default `postgres://fraudnet:fraudnet_dev@localhost:5432/fraudnet_audit`.
- **Kafka:** `KAFKA_BOOTSTRAP_SERVERS`, `SCHEMA_REGISTRY_URL`. Consumer group: `compliance` (split into `compliance-audit` and `compliance-decisions` client_ids).
- **DLQ:** failures route to `*.dlq.v1` per the standard DLQ pattern in `fraudnet.kafka`.

## Runbook

[`docs/runbooks/compliance.md`](../../docs/runbooks/compliance.md)
