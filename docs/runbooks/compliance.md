# Runbook — compliance

## Purpose

Append-only audit-log writer for the platform. Consumes `audit.events.v1` and `decisions.dispatched.v1`, persists to the `fraudnet_audit` Postgres database, and serves regulator-facing reads. Single source of truth for regulator inquiries (CLAUDE.md §7.3).

## SLOs

| Metric | Target |
|---|---|
| Audit write end-to-end (produced → durable in Postgres) p99 | < 2 s |
| Consumer lag on `audit.events.v1` | < 5 s |
| Consumer lag on `decisions.dispatched.v1` | < 5 s |
| `/audit/range` p95 | < 500 ms (1k rows) |
| Availability | 99.95% (audit must not block protected actions upstream) |

## Dashboards

- `rate(compliance_persisted_total[1m]) by (topic)` — should track upstream produce rate ± consumer lag
- `kafka_consumergroup_lag{group="compliance"} by (topic)`
- Postgres `audit_events` row count by partition (sanity for partition rollover)
- `rate(fraudnet_kafka_messages_dlq_total{group="compliance"}[5m])` — non-zero requires investigation

## Alert: consumer lag rising

1. Check Postgres health for the audit DB. Slow inserts → lag.
2. Check pool saturation: `asyncpg` pool sizing in `store.py` is min=2 max=10. Bursts can pin the pool.
3. Scale replicas: `kubectl scale deploy compliance --replicas=N`. Topic partition counts (20 for `audit.events.v1`) cap the parallelism.
4. Sustained >10 min: page DPO liaison — audit gap is a regulatory exposure.

## Alert: DLQ traffic on `audit.events.v1`

Non-zero DLQ on the audit topic is treated as an incident. Audit events are produced by `audit-lib` and are schema-validated upstream — a DLQ event means either a producer bug shipped a malformed event or schema drift. Investigate the DLQ payload, fix at the source, and replay via `tools/replay`.

## Routine: monthly partition rollover

`migrations/0001_audit.sql` bootstraps `audit_events_2026_05` through `audit_events_2026_08`. A scheduled job (Phase 2 — currently manual) runs the next-month CREATE TABLE PARTITION on the 25th of each month. Confirm the next month's partition exists before month-end:

```sql
SELECT inhrelid::regclass FROM pg_inherits WHERE inhparent = 'audit_events'::regclass;
```

If the next month is missing, create it manually and file a ticket against the Phase-2 cron work.

## Routine: Iceberg archive (Phase 2)

Currently manual. Once a month, a senior engineer exports the >6-month-old partition to `s3://fraudnet-lake/audit_archive/` and detaches it from the parent. The detached partition is dropped only after Iceberg checksum verification.

## Regulator export

`GET /audit/export?since=&until=&tenant_id=` streams NDJSON for a date range. Output is signed (Phase 2) and accompanied by an evidence-pack manifest that lists actor, action, resource, purpose for every event. Operators export from the bastion host only — direct external exposure is blocked at the gateway.

## Contacts

- Service team: @mtn-ghana/compliance
- DPO liaison required for any schema change touching `audit_events` or `decision_audits`
- On-call: PagerDuty `fraudnet-compliance`
