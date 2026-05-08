"""Memgraph client wrapper with FraudNet semantics.

Single integration point for graph access. ADR 0002 makes this the seam at
which a future engine swap (e.g. Neo4j) would land. Service code does not
import the Bolt driver directly.

Important: Memgraph does NOT have row-level security. Tenant boundaries in
B2B graph queries (Phase 4) are enforced HERE — every query goes through
`scoped(tenant_id=...)`. Bypassing this is a security defect.
"""

from fraudnet.graph.batch_writer import BufferedGraphWriter, GraphMutation
from fraudnet.graph.client import GraphClient, GraphScope

__all__ = [
    "BufferedGraphWriter",
    "GraphClient",
    "GraphMutation",
    "GraphScope",
]
