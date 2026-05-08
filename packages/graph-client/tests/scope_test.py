from __future__ import annotations

import dataclasses

import pytest

from fraudnet.graph import GraphScope, TenantScopeError


def test_default_tenant_is_mtn_ghana() -> None:
    s = GraphScope()
    assert s.tenant_id == "mtn-ghana"


def test_scope_is_frozen() -> None:
    s = GraphScope(tenant_id="enterprise-acme")
    assert dataclasses.is_dataclass(s)
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.tenant_id = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("slug", "ok"),
    [
        ("mtn-ghana", True),
        ("acme", True),
        ("acme-telecom-123", True),
        ("a", False),               # too short
        ("ACME", False),             # uppercase
        ("0acme", False),            # starts with digit
        ("acme_telecom", False),     # underscore
        ("acme telecom", False),     # space
        ("a" * 64, True),
        ("a" * 65, False),           # too long
    ],
)
def test_tenant_id_slug_is_validated(slug: str, ok: bool) -> None:
    if ok:
        GraphScope(tenant_id=slug)
    else:
        with pytest.raises(TenantScopeError):
            GraphScope(tenant_id=slug)


def test_validate_query_accepts_tenant_scoped() -> None:
    scope = GraphScope(tenant_id="acme")
    scope.validate_query(
        """
        MATCH (n:Number) WHERE n.tenant_id = $tenant_id RETURN n LIMIT 10
        """
    )


def test_validate_query_rejects_unscoped() -> None:
    scope = GraphScope(tenant_id="acme")
    with pytest.raises(TenantScopeError):
        scope.validate_query("MATCH (n:Number) RETURN n LIMIT 10")


def test_tenant_id_clause_helper() -> None:
    scope = GraphScope(tenant_id="acme")
    assert scope.tenant_id_clause() == "n.tenant_id = $tenant_id"
    assert scope.tenant_id_clause(alias="x") == "x.tenant_id = $tenant_id"
