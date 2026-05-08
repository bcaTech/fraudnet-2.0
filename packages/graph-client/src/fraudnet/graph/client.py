"""Memgraph client.

A thin async-friendly wrapper around the Bolt driver that:
  - Forces the active purpose to be set before any read.
  - Enforces tenant scoping at the query layer (CLAUDE.md §12).
  - Exposes a small set of FraudNet-flavoured ops: upsert_number, upsert_wallet,
    add_call_edge, etc. Cypher escape hatch via `cypher()` for ad-hoc reads.

Async support is via run_in_executor; the official driver has an async API
but it pulls in extra deps and the perf delta isn't worth it on FraudNet's
typical query mix.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from neo4j import GraphDatabase, basic_auth

from fraudnet.audit.purpose import require_purpose
from fraudnet.obs import get_logger, histogram

_log = get_logger("fraudnet.graph")

_QUERY_DURATION = histogram(
    "fraudnet_graph_query_seconds",
    "Memgraph query duration.",
    labelnames=("op",),
)


@dataclass(frozen=True)
class GraphScope:
    """Tenant + purpose context for a graph operation.

    Phase 1: only `mtn-ghana` exists. Phase 4 introduces multiple B2B tenants
    and queries gate on `:Tenant {id: $tenant_id}` reachability.
    """

    tenant_id: str = "mtn-ghana"


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

        Refuses to run if no purpose is active in this context — graph reads
        are PII-bearing.
        """
        require_purpose()
        params.setdefault("tenant_id", self._scope.tenant_id)
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
