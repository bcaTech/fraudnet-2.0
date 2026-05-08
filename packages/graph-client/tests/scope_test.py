from __future__ import annotations

from fraudnet.graph.client import GraphScope


def test_default_tenant_is_mtn_ghana() -> None:
    s = GraphScope()
    assert s.tenant_id == "mtn-ghana"


def test_scope_is_frozen() -> None:
    s = GraphScope(tenant_id="enterprise-acme")
    import dataclasses

    assert dataclasses.is_dataclass(s)
    # Frozen dataclasses raise on attempted assignment.
    import pytest

    with pytest.raises(dataclasses.FrozenInstanceError):
        s.tenant_id = "other"  # type: ignore[misc]
