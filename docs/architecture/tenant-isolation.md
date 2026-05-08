# Tenant isolation — defence in depth

Phase 4 introduces multiple B2B tenants on a single FraudNet deployment.
Tenant isolation is enforced at four layers:

```mermaid
flowchart LR
    subgraph CLIENT["B2B client"]
      JWT["Keycloak JWT<br/>tenant_id claim"]
    end

    subgraph EDGE["api-public + api-enterprise middleware"]
      VAL["Token validation<br/>extract_principal"]
      RBAC["@require_role"]
      RL["Per-tenant rate limit<br/>Redis token bucket"]
    end

    subgraph DATA["Data layer"]
      direction LR
      PG["Postgres<br/>RLS on tenant_subscribers,<br/>shared_flags,<br/>enterprise_block_requests<br/>keyed on fraudnet.tenant_id GUC"]
      MG["Memgraph<br/>GraphScope.validate_query<br/>refuses queries without<br/>$tenant_id reference"]
    end

    JWT -->|Bearer| VAL
    VAL -->|Principal{tenant_id}| RBAC
    RBAC -->|allowed| RL
    RL -->|allow / 429| QUERY["Route handler"]
    QUERY -->|tenant_id GUC set per query| PG
    QUERY -->|GraphScope tenant_id| MG
```

## Layer 1 — Edge (auth)

Every request to api-enterprise carries a Keycloak JWT with a
`tenant_id` claim. The middleware decodes the token, validates the
signature against the JWKS, and extracts a `Principal` whose
`tenant_id` is the verified slug. Bearer-less or malformed tokens get
401 before the route handler is dispatched.

## Layer 2 — RBAC

Roles are checked per-route via `@require_role(...)`. Tenant-scoped
routes accept `ENTERPRISE_USER` / `ENTERPRISE_ADMIN`; group-level routes
accept `GROUP_ADMIN` only and are not tenant-scoped (they aggregate
across all tenants by design). Tenant provisioning (`/admin/tenants`)
requires `SYSTEM_ADMIN` plus a fresh step-up token.

## Layer 3 — Rate limit

Per-tenant Redis token bucket. Tunable per-tenant via
`enterprise_tenants.rate_limit_*`. Group-admin paths use a shared
`_group` bucket so a single admin call doesn't drain a tenant's quota.

## Layer 4 — Data

**Postgres.** Every tenant-scoped query carries `tenant_slug`, and the
connection has `fraudnet.tenant_id` set via `set_config`. The RLS
policies on `tenant_subscribers`, `shared_flags`, and
`enterprise_block_requests` filter on
`current_setting('fraudnet.tenant_id')`. RLS is intentionally inert for
GROUP_ADMIN paths (which never set the GUC); their queries are guarded
by RBAC + audit emission instead.

**Memgraph.** Memgraph has no row-level security, so tenant boundaries
are enforced in `fraudnet.graph.GraphClient`:

- Every Cypher query going through `cypher()` is checked against the
  scope's tenant_id. Queries that don't reference `$tenant_id` are
  refused with `TenantScopeError`.
- A caller-supplied `tenant_id` parameter that doesn't match the scope
  is also refused — defence against accidentally querying another
  tenant from inside a request that authenticated as tenant X.
- The `fraudnet_graph_tenant_violations_total` counter exposes refusals
  by reason for dashboards.

## Layer 5 — Audit

Every protected action emits an audit event via `audit-lib`. The audit
log is the single source of truth for regulator inquiries; cross-tenant
access patterns surface in the audit stream and trip alerts.

## What this prevents

| Attack | Where it dies |
| --- | --- |
| Stolen token from tenant A used to read tenant B | Layer 1 (token tenant_id) + Layer 4 (RLS) |
| Tenant A's analyst supplies tenant B's slug in query string | Layer 2 (no role) + Layer 4 (RLS doesn't change with URL params) |
| Bug in api-enterprise sets the wrong tenant_id on the connection | Layer 4 Postgres RLS rejects the query; Memgraph `cypher()` refuses if scope ≠ supplied param |
| New developer writes Cypher without tenant filter | Layer 4 Memgraph: `validate_query` refuses at runtime; CI lint catches it earlier |
