"""B2B enterprise portal API.

Phase 4 service. Each B2B customer is a Keycloak realm tenant; tokens carry
`tenant_id`. Tenant isolation is enforced at every data-layer query: Memgraph
queries through `fraudnet.graph.GraphScope(tenant_id=...)`, Postgres queries
filtered by `tenant_id`, rate limits per-tenant via Redis token-buckets.

Endpoints:
  - GET  /tenant/dashboard          tenant-scoped fraud metrics
  - GET  /tenant/alerts             alerts affecting the tenant's subscribers
  - POST /tenant/report             submit fraud intelligence
  - GET  /tenant/shared-flags       flags shared with / from this tenant
  - POST /tenant/block-request      request a cross-network block

Group-level (GROUP_ADMIN role only):
  - GET  /group/overview            aggregate fraud metrics across opcos
  - GET  /group/cross-opco-rings    rings detected via federation
  - GET  /group/trending-motifs     motif patterns trending across the group

Admin (SYSTEM_ADMIN, step-up):
  - POST /admin/tenants             provision a new tenant
"""
