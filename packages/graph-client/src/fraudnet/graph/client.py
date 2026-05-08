"""Memgraph client.

A thin async-friendly wrapper around the Bolt driver that:
  - Forces the active purpose to be set before any read.
  - Enforces tenant scoping at the query layer (CLAUDE.md §12).
  - Exposes a small set of FraudNet-flavoured ops: upsert_number, upsert_wallet,
    add_call_edge, etc. Cypher escape hatch via `cypher()` for ad-hoc reads.

Async support is via run_in_executor; the official driver has an async API
but it pulls in extra deps and the perf delta isn't worth it on FraudNet's
typical query mix.

Tenant isolation (Phase 4): Memgraph has no row-level security, so every
query is enforced here. `GraphScope.validate_query` runs at the API
boundary on every ad-hoc Cypher: a query without a tenant_id parameter
reference (or one that mentions a tenant other than the scope's) is
refused before reaching the driver. The fast-path `_run` write helpers
build their own tenant clauses from `scope.tenant_id` and never accept
caller-provided tenant_id values.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from neo4j import GraphDatabase, basic_auth

from fraudnet.audit.purpose import require_purpose
from fraudnet.obs import counter, get_logger, histogram

_log = get_logger("fraudnet.graph")

_QUERY_DURATION = histogram(
    "fraudnet_graph_query_seconds",
    "Memgraph query duration.",
    labelnames=("op",),
)
_TENANT_VIOLATIONS = counter(
    "fraudnet_graph_tenant_violations_total",
    "Cypher queries refused for missing or mismatched tenant scoping.",
    labelnames=("reason",),
)


# Slug pattern enforced for tenant_id values reaching the graph layer.
# Lowercase alphanumeric + hyphen; ≤ 64 chars. Matches the api-enterprise
# tenant slug rule (services/api-enterprise/src/api_enterprise/api.py).
_TENANT_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")


class TenantScopeError(ValueError):
    """Raised when a Cypher query is not properly tenant-scoped.

    This is a programming error, not a runtime condition — the only
    way to hit it is to bypass the API layer's GraphScope. Catching
    it should never be appropriate; fix the offending query.
    """


@dataclass(frozen=True)
class GraphScope:
    """Tenant + purpose context for a graph operation.

    Phase 4 hardening: tenant_id is the unit of B2B isolation. Every
    Cypher query going through `GraphClient` is checked against this
    scope; queries without a tenant filter are refused (`validate_query`
    rejects them). Per-tenant rate limiting and audit emission happens
    a layer up (the API service); this layer guarantees the underlying
    graph store cannot leak across tenants by accident.
    """

    tenant_id: str = "mtn-ghana"

    def __post_init__(self) -> None:
        if not _TENANT_SLUG_RE.match(self.tenant_id):
            raise TenantScopeError(
                f"invalid tenant_id slug: {self.tenant_id!r} "
                "(must be lowercase alphanumeric + hyphen, ≤ 64 chars)"
            )

    def validate_query(self, query: str) -> None:
        """Refuse queries that do not reference $tenant_id.

        Heuristic but strict: the query must mention `$tenant_id` (the
        Cypher parameter name we standardise on). Queries that bind tenant
        in a non-standard way must call the underlying driver directly,
        which is reserved for the writer (`batch_writer.py`) — and that
        path constructs the clause itself from the scope.
        """
        if "$tenant_id" not in query:
            _TENANT_VIOLATIONS.labels(reason="no_tenant_param").inc()
            raise TenantScopeError(
                "tenant-scoped Cypher must reference the $tenant_id parameter; "
                "use scope.tenant_id_clause() to add the standard filter"
            )

    def tenant_id_clause(self, *, alias: str = "n") -> str:
        """Standard 'tenant filter' Cypher fragment for inclusion in WHERE
        clauses. Returns a string ready to drop into a WHERE list, parameter-
        bound to $tenant_id (Memgraph parameter binding is automatic when the
        query is run with the tenant_id kwarg)."""
        return f"{alias}.tenant_id = $tenant_id"


class GraphClient:
    def __init__(
        self,
        *,
        bolt_url: str = "bolt://localhost:7687",
        auth: tuple[str, str] | None = None,
    ) -> None:
        self._driver = GraphDatabase.driver(
            bolt_url,
            auth=basic_auth(*auth) if auth else None,
        )

    @asynccontextmanager
    async def session(self, scope: GraphScope) -> AsyncIterator["_Session"]:
        # Memgraph does not implement Neo4j's database concept the same way;
        # we use the default database. Tenant scoping is by query parameter.
        loop = asyncio.get_running_loop()
        session = await loop.run_in_executor(None, self._driver.session)
        try:
            yield _Session(session, scope)
        finally:
            await loop.run_in_executor(None, session.close)

    async def close(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._driver.close)


class _Session:
    def __init__(self, neo_session: Any, scope: GraphScope) -> None:
        self._s = neo_session
        self._scope = scope

    async def upsert_number(self, msisdn: str, *, risk_score: float | None = None) -> None:
        # Writes do not require a purpose claim; reads do.
        await self._run(
            "upsert_number",
            """
            MERGE (n:Number {msisdn: $msisdn, tenant_id: $tenant_id})
            ON CREATE SET n.created_at = timestamp()
            SET n.risk_score = coalesce($risk_score, n.risk_score),
                n.updated_at = timestamp()
            """,
            msisdn=msisdn,
            tenant_id=self._scope.tenant_id,
            risk_score=risk_score,
        )

    async def upsert_wallet(self, wallet_id: str, *, owner_msisdn: str | None = None) -> None:
        await self._run(
            "upsert_wallet",
            """
            MERGE (w:Wallet {wallet_id: $wallet_id, tenant_id: $tenant_id})
            ON CREATE SET w.created_at = timestamp()
            WITH w
            FOREACH (_ IN CASE WHEN $owner_msisdn IS NULL THEN [] ELSE [1] END |
                MERGE (n:Number {msisdn: $owner_msisdn, tenant_id: $tenant_id})
                MERGE (n)-[:OWNS]->(w)
            )
            """,
            wallet_id=wallet_id,
            owner_msisdn=owner_msisdn,
            tenant_id=self._scope.tenant_id,
        )

    async def add_call_edge(
        self,
        *,
        caller: str,
        callee: str,
        ts_ms: int,
        duration_s: int | None = None,
    ) -> None:
        await self._run(
            "add_call_edge",
            """
            MERGE (a:Number {msisdn: $caller, tenant_id: $tenant_id})
            MERGE (b:Number {msisdn: $callee, tenant_id: $tenant_id})
            CREATE (a)-[:CALLED {ts: $ts_ms, duration: $duration_s}]->(b)
            """,
            caller=caller,
            callee=callee,
            ts_ms=ts_ms,
            duration_s=duration_s,
            tenant_id=self._scope.tenant_id,
        )

    async def add_money_flow_edge(
        self,
        *,
        sender: str,
        recipient: str,
        ts_ms: int,
        amount_minor: int,
    ) -> None:
        await self._run(
            "add_money_flow_edge",
            """
            MERGE (a:Wallet {wallet_id: $sender, tenant_id: $tenant_id})
            MERGE (b:Wallet {wallet_id: $recipient, tenant_id: $tenant_id})
            CREATE (a)-[:SENT {ts: $ts_ms, amount: $amount_minor}]->(b)
            """,
            sender=sender,
            recipient=recipient,
            ts_ms=ts_ms,
            amount_minor=amount_minor,
            tenant_id=self._scope.tenant_id,
        )

    async def cypher(
        self,
        query: str,
        *,
        op: str,
        **params: Any,
    ) -> list[dict[str, Any]]:
        """Run an ad-hoc read query.

        Refuses to run if:
          - no purpose is active in this context (graph reads are PII-bearing),
          - the query does not reference $tenant_id (tenant scoping is
            mandatory in Phase 4), or
          - a caller-supplied tenant_id parameter does not match the scope's
            tenant_id (defence against accidentally querying another tenant
            from inside a request that authenticated as tenant X).
        """
        require_purpose()
        self._scope.validate_query(query)
        supplied = params.get("tenant_id")
        if supplied is not None and supplied != self._scope.tenant_id:
            _TENANT_VIOLATIONS.labels(reason="tenant_mismatch").inc()
            raise TenantScopeError(
                f"tenant_id mismatch: scope={self._scope.tenant_id!r}, "
                f"query param={supplied!r}"
            )
        params["tenant_id"] = self._scope.tenant_id
        return await self._read(op, query, **params)

    async def _run(self, op: str, query: str, **params: Any) -> None:
        loop = asyncio.get_running_loop()

        def _do() -> None:
            with _QUERY_DURATION.labels(op=op).time():
                self._s.run(query, **params).consume()

        await loop.run_in_executor(None, _do)

    async def _read(self, op: str, query: str, **params: Any) -> list[dict[str, Any]]:
        loop = asyncio.get_running_loop()

        def _do() -> list[dict[str, Any]]:
            with _QUERY_DURATION.labels(op=op).time():
                result = self._s.run(query, **params)
                return [dict(record) for record in result]

        return await loop.run_in_executor(None, _do)
