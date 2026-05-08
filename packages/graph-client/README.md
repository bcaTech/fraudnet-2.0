# graph-client

Memgraph client wrapper with FraudNet semantics. Single integration point
for graph access; service code never imports the bolt driver directly.

## Tenant scoping (Phase 4)

Memgraph has no row-level security, so multi-tenant isolation is enforced
in `GraphClient`. Every Cypher query going through `cypher()` is checked:

- The query must reference `$tenant_id` (the parameter we standardise
  on). Queries without it raise `TenantScopeError`.
- A caller-supplied `tenant_id` parameter that doesn't match the active
  scope's `tenant_id` raises `TenantScopeError` — defence against a
  buggy route from accidentally querying another tenant.
- The slug pattern is enforced at `GraphScope` construction; invalid
  slugs are refused before any driver call.

The `fraudnet_graph_tenant_violations_total` counter exposes refusals
by reason (`no_tenant_param`, `tenant_mismatch`) for dashboards.

## Usage

```python
from fraudnet.audit import with_purpose
from fraudnet.graph import GraphClient, GraphScope
from fraudnet.schemas.types import Purpose

client = GraphClient(bolt_url="bolt://memgraph:7687")
scope = GraphScope(tenant_id="mtn-ghana")

with with_purpose(Purpose.FRAUD_PREVENTION):
    async with client.session(scope) as session:
        rows = await session.cypher(
            "MATCH (n:Number) WHERE n.tenant_id = $tenant_id RETURN n LIMIT 10",
            op="list_numbers",
        )
```

The `session.cypher()` path requires both an active purpose claim
(audit-lib) and a tenant-scoped query (this layer). Bypassing either is
a security defect; the Bolt driver is private.

## Phase 4 integration points

- `services/api-enterprise` — every B2B route that touches Memgraph
  uses this layer. Tenant slugs are validated upstream by Keycloak
  token decode + `extract_principal`.
- `services/brain-graph` — the analyser's federation client uses
  `hash_identifier` to hash before sending; the local Memgraph queries
  go through `GraphScope(tenant_id="mtn-ghana")`.
- `packages/federation` — the production server adapter constructs
  hashed read views via this layer with the correct scope.

## Schema

Node + edge types in `schema.cypher`. Indexes mandatory on
`Number.msisdn`, `Wallet.wallet_id`, `Device.imei`, `Ring.ring_id`, and
edge `ts` properties.
