# api-enterprise

B2B portal API for FraudNet 2.0 (Phase 4). Each enterprise customer is a
Keycloak realm tenant; tokens carry `tenant_id`. Tenant isolation is enforced
at every data-layer boundary:

- Postgres: every query carries `tenant_slug`; row-level security on
  `tenant_subscribers`, `shared_flags`, and `enterprise_block_requests` is
  keyed on the connection-scoped GUC `fraudnet.tenant_id`.
- Memgraph: every query goes through `GraphScope(tenant_id=...)` (CLAUDE.md
  §12); the graph layer refuses untyped reads.
- Rate limit: per-tenant Redis-backed token bucket. Tunable per-tenant via
  `enterprise_tenants.rate_limit_*` and reloaded on tenant update.

## Endpoints

### Tenant-scoped (`ENTERPRISE_USER` / `ENTERPRISE_ADMIN`)

| Method | Path | Description |
| --- | --- | --- |
| GET  | `/tenant/dashboard`     | Fraud KPIs scoped to the tenant's subscribers |
| GET  | `/tenant/alerts`        | Alerts whose subject matches a tenant subscriber |
| POST | `/tenant/report`        | Submit fraud intelligence (forwarded to `intel.events.v1`) |
| GET  | `/tenant/shared-flags`  | Hashed flags shared with / from this tenant |
| POST | `/tenant/block-request` | Request a cross-network block (`ENTERPRISE_ADMIN` only) |

### Group-level (`GROUP_ADMIN`)

Cross-tenant aggregates. Not tenant-scoped.

| Method | Path | Description |
| --- | --- | --- |
| GET | `/group/overview`         | Group fraud KPIs (active tenants, open alerts, severity mix) |
| GET | `/group/cross-opco-rings` | Rings where membership crosses opcos (federation-detected) |
| GET | `/group/trending-motifs`  | Motif patterns by hits / distinct tenants |

### Admin (`SYSTEM_ADMIN` + step-up)

| Method | Path | Description |
| --- | --- | --- |
| POST | `/admin/tenants` | Provision a new tenant (creates `:Tenant` graph node) |
| GET  | `/admin/tenants` | List tenants |

## Local dev

```bash
make dev SERVICE=api-enterprise
```

Provision a tenant in dev:

```bash
curl -sS -X POST http://localhost:8013/admin/tenants \
  -H 'Authorization: Bearer <admin-jwt-with-step-up>' \
  -H 'Content-Type: application/json' \
  -d '{"slug":"acme","name":"Acme Telecom","contact_email":"sec@acme.example"}'
```

## Migrations

`migrations/0001_enterprise_schema.sql` creates the four tenant tables and
applies RLS policies. Run via the project-wide migration tool; the schema
co-exists with the core FraudNet tables.

## Tests

```bash
pytest services/api-enterprise -v
```
