"""End-to-end tenant isolation: a tenant's session cannot read another
tenant's data.

The test uses an in-memory fake of the bolt driver — the cypher() path
runs through validation + parameter binding, and the fake refuses any
query whose `$tenant_id` does not match the stored data's tenant_id.

This is an integration test of the GraphScope contract, not of Memgraph
itself.
"""

from __future__ import annotations

from typing import Any

import pytest

from fraudnet.audit import with_purpose
from fraudnet.graph import GraphScope, TenantScopeError
from fraudnet.graph.client import GraphClient
from fraudnet.schemas.types import Purpose


class _FakeNeoSession:
    """Tracks an in-memory tenant-scoped store + records every query."""

    def __init__(self, store: dict[str, list[dict[str, Any]]]) -> None:
        self._store = store
        self.queries: list[tuple[str, dict[str, Any]]] = []

    def run(self, query: str, **params: Any) -> "_FakeResult":
        self.queries.append((query, params))
        tenant_id = params.get("tenant_id")
        rows = [r for r in self._store.get(tenant_id, []) if r.get("tenant_id") == tenant_id]
        return _FakeResult(rows)

    def close(self) -> None:
        return None


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def __iter__(self):  # noqa: ANN204
        return iter(self._rows)

    def consume(self) -> None:
        return None


class _FakeDriver:
    def __init__(self, store: dict[str, list[dict[str, Any]]]) -> None:
        self._store = store
        self.last_session: _FakeNeoSession | None = None

    def session(self) -> _FakeNeoSession:
        s = _FakeNeoSession(self._store)
        self.last_session = s
        return s

    def close(self) -> None:
        return None


def _client_with_store(
    store: dict[str, list[dict[str, Any]]]
) -> tuple[GraphClient, _FakeDriver]:
    client = GraphClient.__new__(GraphClient)
    driver = _FakeDriver(store)
    client._driver = driver  # type: ignore[attr-defined]
    return client, driver


# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_a_cannot_read_tenant_b_data() -> None:
    """Tenant A's GraphScope only ever surfaces tenant A's rows."""
    store = {
        "tenant-a": [{"msisdn": "+233A", "tenant_id": "tenant-a"}],
        "tenant-b": [{"msisdn": "+233B", "tenant_id": "tenant-b"}],
    }
    client, driver = _client_with_store(store)

    scope_a = GraphScope(tenant_id="tenant-a")
    with with_purpose(Purpose.FRAUD_PREVENTION):
        async with client.session(scope_a) as session:
            rows = await session.cypher(
                "MATCH (n:Number) WHERE n.tenant_id = $tenant_id RETURN n",
                op="test",
            )

    assert len(rows) == 1
    # Driver received tenant-a as the bound parameter.
    assert driver.last_session is not None
    sent = driver.last_session.queries[0]
    assert sent[1]["tenant_id"] == "tenant-a"


@pytest.mark.asyncio
async def test_caller_cannot_override_tenant_id_param() -> None:
    """If a route bug supplies the wrong tenant_id, the client refuses
    the call before the bolt driver sees it."""
    store: dict[str, list[dict[str, Any]]] = {}
    client, _ = _client_with_store(store)

    scope_a = GraphScope(tenant_id="tenant-a")
    with with_purpose(Purpose.FRAUD_PREVENTION):
        async with client.session(scope_a) as session:
            with pytest.raises(TenantScopeError, match="tenant_id mismatch"):
                await session.cypher(
                    "MATCH (n) WHERE n.tenant_id = $tenant_id RETURN n",
                    op="test",
                    tenant_id="tenant-b",  # malicious / buggy override
                )


@pytest.mark.asyncio
async def test_unscoped_query_is_refused() -> None:
    """A query that does not reference $tenant_id is refused."""
    store: dict[str, list[dict[str, Any]]] = {}
    client, _ = _client_with_store(store)

    scope = GraphScope(tenant_id="tenant-a")
    with with_purpose(Purpose.FRAUD_PREVENTION):
        async with client.session(scope) as session:
            with pytest.raises(TenantScopeError, match="must reference"):
                await session.cypher(
                    "MATCH (n:Number) RETURN n",  # missing $tenant_id
                    op="test",
                )


@pytest.mark.asyncio
async def test_purpose_required_before_read() -> None:
    """Reading without an active purpose is refused (audit-lib §7.2)."""
    from fraudnet.audit import PurposeMissingError

    store: dict[str, list[dict[str, Any]]] = {}
    client, _ = _client_with_store(store)

    scope = GraphScope(tenant_id="tenant-a")
    # No `with_purpose(...)` — must fail before the query even validates.
    async with client.session(scope) as session:
        with pytest.raises(PurposeMissingError):
            await session.cypher(
                "MATCH (n:Number) WHERE n.tenant_id = $tenant_id RETURN n",
                op="test",
            )
