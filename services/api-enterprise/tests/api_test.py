"""api-enterprise route tests using a fake principal + in-memory deps.

Covers:
  - GROUP_ADMIN-only routes refuse ENTERPRISE_USER tokens.
  - SYSTEM_ADMIN routes refuse without step-up.
  - Tenant-scoped routes carry the principal's tenant_id through to the repo.
  - Slug validation rejects malformed tenant identifiers.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from fraudnet.auth.principal import Principal, Role
from api_enterprise.api import _hash_identifier, _valid_slug
from api_enterprise.main import create_app
from api_enterprise.rate_limit import InMemoryRateLimiter, RateLimitConfig


@pytest.fixture(autouse=True)
def _isolate_audit_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Audit emits to Kafka in production; bypass for unit tests."""

    async def _noop(*_args: Any, **_kwargs: Any) -> None:
        return None

    import fraudnet.audit
    monkeypatch.setattr(fraudnet.audit, "record", _noop)
    import api_enterprise.api
    monkeypatch.setattr(api_enterprise.api, "record", _noop)


def _principal(*roles: Role, tenant_id: str = "acme", step_up: bool = False) -> Principal:
    return Principal(
        subject=str(uuid4()),
        actor_kind="user",
        roles=frozenset(roles),
        tenant_id=tenant_id,
        step_up_at_ms=int(time.time() * 1000) if step_up else None,
    )


class _FakeAlertRepo:
    async def list(self, **_: Any) -> list[dict[str, Any]]:
        return []

    async def dashboard(self, *, tenant_id: str) -> dict[str, Any]:
        return {
            "open_alerts": 0,
            "recent_24h": 0,
            "recent_7d": 0,
            "by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0},
            "blocked_24h": 0,
        }


class _FakeSharedRepo:
    async def list_for_tenant(self, **_: Any) -> list[dict[str, Any]]:
        return []

    async def submit(self, **_: Any) -> dict[str, Any]:
        return {}


class _FakeBlockRepo:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def submit(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        return {
            "id": uuid4(),
            "tenant_slug": kw["tenant_id"],
            "target_kind": kw["target_kind"],
            "target_value": kw["target_value"],
            "reason": kw["reason"],
            "status": "pending_review",
            "requested_at": "2026-05-08T00:00:00+00:00",
            "decided_at": None,
            "decision_notes": None,
        }


class _FakeTenantRepo:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def list(self, **_: Any) -> list[dict[str, Any]]:
        return []

    async def get(self, *, tenant_id: str) -> dict[str, Any] | None:
        return None

    async def create(self, **kw: Any) -> dict[str, Any]:
        row = {
            "id": uuid4(),
            "slug": kw["slug"],
            "name": kw["name"],
            "status": "active",
            "federation_enabled": kw["federation_enabled"],
            "rate_limit_capacity": kw["rate_limit_capacity"],
            "rate_limit_refill_per_s": kw["rate_limit_refill_per_s"],
            "contact_email": kw["contact_email"],
            "created_at": "2026-05-08T00:00:00+00:00",
            "updated_at": "2026-05-08T00:00:00+00:00",
        }
        self.created.append(row)
        return row


class _FakeGroupRepo:
    async def overview(self) -> dict[str, Any]:
        return {
            "active_tenants": 3,
            "open_alerts": 12,
            "recent_24h": 4,
            "by_severity": {"critical": 1, "high": 2},
            "distinct_subjects": 9,
            "cross_opco_rings": 0,
        }

    async def cross_opco_rings(self, **_: Any) -> list[dict[str, Any]]:
        return []

    async def trending_motifs(self, **_: Any) -> list[dict[str, Any]]:
        return []


class _FakeGraph:
    """Minimal stand-in supporting `session(...)` async context manager."""

    class _Session:
        async def cypher(self, *_a: Any, **_kw: Any) -> list[dict[str, Any]]:
            return []

    def session(self, _scope: Any) -> Any:
        sess = _FakeGraph._Session()

        class _CM:
            async def __aenter__(self_inner) -> Any:
                return sess

            async def __aexit__(self_inner, *exc: Any) -> None:
                return None

        return _CM()

    async def close(self) -> None:
        return None


def _build_test_app(principal: Principal) -> TestClient:
    """App with all repos and dependencies replaced by in-memory fakes."""
    app = create_app(
        db=object(),  # type: ignore[arg-type] — repos override; never queried
        graph=_FakeGraph(),  # type: ignore[arg-type]
        rate_limiter=InMemoryRateLimiter(
            config=RateLimitConfig(capacity=1000, refill_per_s=1000.0)
        ),
        intel_producer=_FakeIntelProducer(),
        test_principal=principal,
    )

    # Skip lifespan startup (which would try to wire real DB / Redis / etc.).
    # Manually populate the state the routes touch.
    app.state.alerts = _FakeAlertRepo()
    app.state.shared = _FakeSharedRepo()
    app.state.blocks = _FakeBlockRepo()
    app.state.tenants = _FakeTenantRepo()
    app.state.group = _FakeGroupRepo()
    app.state.graph = _FakeGraph()
    app.state.federation = None
    app.state.intel_producer = _FakeIntelProducer()
    app.state.rate_limiter = InMemoryRateLimiter(
        config=RateLimitConfig(capacity=1000, refill_per_s=1000.0)
    )
    return TestClient(app)


class _FakeIntelProducer:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    async def send(self, event: Any, *, key: str | None = None) -> None:
        self.sent.append((event, key))

    async def stop(self) -> None:
        return None


# ---------------------------------------------------------------------------


def test_health_live() -> None:
    client = _build_test_app(_principal(Role.ENTERPRISE_USER))
    r = client.get("/health/live")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_dashboard_requires_enterprise_role() -> None:
    client = _build_test_app(_principal(Role.CUSTOMER))
    r = client.get("/tenant/dashboard")
    assert r.status_code == 403


def test_dashboard_returns_tenant_scoped_metrics() -> None:
    client = _build_test_app(_principal(Role.ENTERPRISE_USER, tenant_id="acme"))
    r = client.get("/tenant/dashboard")
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == "acme"
    assert "by_severity" in body


def test_group_endpoints_refuse_enterprise_user() -> None:
    client = _build_test_app(_principal(Role.ENTERPRISE_USER))
    for path in ("/group/overview", "/group/cross-opco-rings", "/group/trending-motifs"):
        r = client.get(path)
        assert r.status_code == 403, f"{path} should be forbidden for ENTERPRISE_USER"


def test_group_overview_allowed_for_group_admin() -> None:
    client = _build_test_app(_principal(Role.GROUP_ADMIN))
    r = client.get("/group/overview")
    assert r.status_code == 200
    assert r.json()["active_tenants"] == 3


def test_create_tenant_requires_step_up() -> None:
    # SYSTEM_ADMIN without step-up — must fail step-up gate.
    client = _build_test_app(_principal(Role.SYSTEM_ADMIN, step_up=False))
    r = client.post(
        "/admin/tenants",
        json={
            "slug": "acme",
            "name": "Acme",
            "contact_email": "sec@acme.example",
        },
    )
    assert r.status_code in (401, 403)


def test_create_tenant_with_step_up() -> None:
    client = _build_test_app(_principal(Role.SYSTEM_ADMIN, step_up=True))
    r = client.post(
        "/admin/tenants",
        json={
            "slug": "acme",
            "name": "Acme Telecom",
            "contact_email": "sec@acme.example",
            "federation_enabled": True,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "acme"
    assert body["federation_enabled"] is True


def test_block_request_requires_admin() -> None:
    """ENTERPRISE_USER cannot file a block request — only ENTERPRISE_ADMIN."""
    client = _build_test_app(_principal(Role.ENTERPRISE_USER))
    r = client.post(
        "/tenant/block-request",
        json={
            "target_kind": "msisdn",
            "target_value": "+233200000001",
            "reason": "confirmed mule wallet — repeated offender",
        },
    )
    assert r.status_code == 403


def test_block_request_short_reason_rejected() -> None:
    client = _build_test_app(_principal(Role.ENTERPRISE_ADMIN))
    r = client.post(
        "/tenant/block-request",
        json={
            "target_kind": "msisdn",
            "target_value": "+233200000001",
            "reason": "short",
        },
    )
    assert r.status_code == 400


@pytest.mark.parametrize(
    ("slug", "ok"),
    [
        ("acme", True),
        ("acme-telecom", True),
        ("a", False),         # too short (< 2)
        ("ACME", False),      # uppercase
        ("0acme", False),     # starts with digit
        ("acme_telecom", False),  # underscore
        ("a" * 64, True),
        ("a" * 65, False),    # too long
    ],
)
def test_slug_validator(slug: str, ok: bool) -> None:
    assert _valid_slug(slug) is ok


def test_hash_identifier_is_deterministic_and_kind_sensitive() -> None:
    """Same value, different kind → different hash. PII never crosses; tests
    catch any change to the wire format."""
    h_msisdn = _hash_identifier("+233200000001", kind="msisdn")
    h_wallet = _hash_identifier("+233200000001", kind="wallet")
    h_msisdn_dup = _hash_identifier("+233200000001", kind="msisdn")
    assert h_msisdn == h_msisdn_dup
    assert h_msisdn != h_wallet
    assert len(h_msisdn) == 64  # sha-256 hex


def test_rate_limit_returns_429() -> None:
    """Drain a tenant bucket and assert the next request is throttled."""
    app = create_app(
        db=object(),  # type: ignore[arg-type]
        graph=_FakeGraph(),  # type: ignore[arg-type]
        rate_limiter=InMemoryRateLimiter(
            config=RateLimitConfig(capacity=1, refill_per_s=0)
        ),
        intel_producer=_FakeIntelProducer(),
        test_principal=_principal(Role.ENTERPRISE_USER, tenant_id="acme"),
    )
    app.state.alerts = _FakeAlertRepo()
    app.state.shared = _FakeSharedRepo()
    app.state.blocks = _FakeBlockRepo()
    app.state.tenants = _FakeTenantRepo()
    app.state.group = _FakeGroupRepo()
    app.state.graph = _FakeGraph()
    app.state.federation = None
    app.state.intel_producer = _FakeIntelProducer()
    app.state.rate_limiter = InMemoryRateLimiter(
        config=RateLimitConfig(capacity=1, refill_per_s=0)
    )
    client = TestClient(app)
    r1 = client.get("/tenant/dashboard")
    assert r1.status_code == 200
    r2 = client.get("/tenant/dashboard")
    assert r2.status_code == 429
