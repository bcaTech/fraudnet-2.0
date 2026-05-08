"""api-noc — NOC investigator workbench API.

Auth: Keycloak JWT bearer tokens. RBAC via @require_role decorators
(CLAUDE.md §7.1). Tenant scoping at the data layer; for Phase 1 there's
only the mtn-ghana tenant but the queries already carry tenant_id.

Reads compose Postgres (alerts, rings, takedowns) and Memgraph (ring graph
view, ad-hoc subgraph queries). Postgres queries go through a thin
repository layer. Memgraph queries go through fraudnet.graph.GraphClient.

The takedown workflow is a state machine; transitions are guarded
server-side and audited via fraudnet.audit.record() with a
purpose=fraud_prevention claim.
"""

__version__ = "0.1.0"
