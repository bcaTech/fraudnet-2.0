# Runbook — api-noc

## Purpose

Investigator workbench API. The face of FraudNet 2.0 to NOC analysts.

## SLOs

| Endpoint | p99 |
|---|---|
| `/alerts` (list) | < 250 ms |
| `/alerts/{id}` | < 100 ms |
| `/rings/{id}` | < 250 ms |
| `/rings/{id}/graph` | < 500 ms |
| Takedown transitions | < 200 ms |

## Dashboards

- `histogram_quantile(0.99, sum by (le, route) (rate(fraudnet_request_duration_seconds_bucket{service="api-noc"}[5m])))`
- `rate(fraudnet_requests_total{service="api-noc",status=~"5.."}[5m])` — 5xx surface

## Alert: 5xx rate elevated

1. Check Postgres connection pool. asyncpg failures bubble through to 503.
2. Check Memgraph. Ring-graph endpoint depends on Bolt connectivity.
3. Check Keycloak. JWKS unreachable returns 401, not 5xx — but a long timeout looks like one.

## Takedown filed = high-impact action

Filed takedowns trigger downstream regulator workflows. Audit log carries actor, ring, filed_with, and metadata. Any incident around an accidental filing should pull the audit trail first via `compliance` queries.

## Migrations

Apply `migrations/0001_initial.sql` against the `fraudnet` database. Phase 2 adds Alembic. Run before first deploy of a new environment:

```bash
psql "$POSTGRES_DSN" -f services/api-noc/migrations/0001_initial.sql
```

## Contacts

- Service team: @mtn-ghana/noc
- DPO review required for any change touching PII fields
- On-call: PagerDuty `fraudnet-noc`
