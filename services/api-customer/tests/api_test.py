"""api-customer endpoint tests.

Wires create_app with in-memory deps (FakeIntelProducer, FakePool) so we
exercise the FastAPI routing, schema validation, and audit-record paths
without bringing up Postgres or Kafka.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_customer.api import router
from api_customer.otp import InMemoryOtpAdapter
from api_customer.session import SessionTokenIssuer


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeIntelProducer:
    def __init__(self) -> None:
        self.sent: list[tuple[Any, str | None]] = []

    async def start(self) -> None: return None

    async def send(self, payload: Any, *, key: str | None = None) -> None:
        self.sent.append((payload, key))

    async def stop(self) -> None: return None


class _FakeConn:
    def __init__(self, pool: FakePool) -> None:
        self._pool = pool

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:  # noqa: ARG002
        self._pool.calls.append(("fetch", sql, args))
        return list(self._pool.next_fetch)

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:  # noqa: ARG002
        self._pool.calls.append(("fetchrow", sql, args))
        return self._pool.next_fetchrow


class FakePool:
    """Minimal asyncpg.Pool stand-in — supports acquire() context manager."""

    def __init__(self) -> None:
        self.next_fetch: list[dict[str, Any]] = []
        self.next_fetchrow: dict[str, Any] | None = None
        self.calls: list[tuple[str, str, tuple[Any, ...]]] = []

    @asynccontextmanager
    async def acquire(self):  # type: ignore[no-untyped-def]
        yield _FakeConn(self)

    async def close(self) -> None:
        return None


def _build_app() -> tuple[FastAPI, FakeIntelProducer, FakePool, str]:
    app = FastAPI()
    intel = FakeIntelProducer()
    pool = FakePool()
    issuer = SessionTokenIssuer(secret="test-secret", ttl_s=600)
    token, _ = issuer.issue(msisdn="+233241234567")

    app.state.otp = InMemoryOtpAdapter()
    app.state.pool = pool
    app.state.intel_producer = intel
    app.state.session = issuer
    app.include_router(router)
    return app, intel, pool, token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# /me/ott-alerts
# ---------------------------------------------------------------------------


class TestOttAlerts:
    def test_returns_alerts_for_subscriber(self) -> None:
        app, _, pool, token = _build_app()
        pool.next_fetch = [
            {
                "id": uuid4(),
                "severity": "high",
                "score": 0.92,
                "status": "new",
                "details": {
                    "domain": "phish.example.com",
                    "signal_kind": "data.dns_blocklist_hit",
                },
                "created_at": datetime.now(timezone.utc),
            }
        ]
        with TestClient(app) as client:
            r = client.get("/me/ott-alerts", headers=_auth(token))
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["domain"] == "phish.example.com"
        assert body[0]["signal_kind"] == "data.dns_blocklist_hit"

    def test_unauthenticated_401(self) -> None:
        app, _, _, _ = _build_app()
        with TestClient(app) as client:
            r = client.get("/me/ott-alerts")
        assert r.status_code == 401

    def test_empty_returns_empty_list(self) -> None:
        app, _, _, token = _build_app()  # pool.next_fetch defaults to []
        with TestClient(app) as client:
            r = client.get("/me/ott-alerts", headers=_auth(token))
        assert r.status_code == 200
        assert r.json() == []


# ---------------------------------------------------------------------------
# /me/report-url
# ---------------------------------------------------------------------------


class TestReportUrl:
    def test_happy_path_emits_intel_event(self) -> None:
        app, intel, _, token = _build_app()
        with TestClient(app) as client:
            r = client.post(
                "/me/report-url",
                json={"url": "https://phish.example.com/win"},
                headers=_auth(token),
            )
        assert r.status_code == 200
        assert r.json()["status"] == "received"
        assert len(intel.sent) == 1
        sent, key = intel.sent[0]
        assert sent.indicator == "https://phish.example.com/win"
        assert sent.indicator_kind.value == "url"
        assert key == "https://phish.example.com/win"
        # attribution carries the customer's MSISDN so the fraud team can
        # correlate without re-querying the session.
        assert "+233241234567" in (sent.attribution or "")

    def test_empty_url_400(self) -> None:
        app, _, _, token = _build_app()
        with TestClient(app) as client:
            r = client.post(
                "/me/report-url",
                json={"url": "   "},
                headers=_auth(token),
            )
        assert r.status_code == 400

    def test_overlong_url_400(self) -> None:
        app, _, _, token = _build_app()
        with TestClient(app) as client:
            r = client.post(
                "/me/report-url",
                json={"url": "https://" + "a" * 4096},
                headers=_auth(token),
            )
        assert r.status_code == 400

    def test_unauthenticated_401(self) -> None:
        app, _, _, _ = _build_app()
        with TestClient(app) as client:
            r = client.post("/me/report-url", json={"url": "https://x.example"})
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# /me/blocked-domains
# ---------------------------------------------------------------------------


class TestBlockedDomains:
    def test_returns_aggregated_domains(self) -> None:
        app, _, pool, token = _build_app()
        now = datetime.now(timezone.utc)
        pool.next_fetch = [
            {
                "domain": "phish.example.com",
                "category": "phishing",
                "first_blocked_at": now,
                "last_blocked_at": now,
                "block_count": 5,
            },
            {
                "domain": "scam.example.com",
                "category": None,
                "first_blocked_at": now,
                "last_blocked_at": now,
                "block_count": 1,
            },
        ]
        with TestClient(app) as client:
            r = client.get("/me/blocked-domains", headers=_auth(token))
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 2
        assert body[0]["domain"] == "phish.example.com"
        assert body[0]["block_count"] == 5

    def test_filters_rows_with_no_domain(self) -> None:
        app, _, pool, token = _build_app()
        now = datetime.now(timezone.utc)
        pool.next_fetch = [
            {
                "domain": "",
                "category": None,
                "first_blocked_at": now,
                "last_blocked_at": now,
                "block_count": 1,
            },
        ]
        with TestClient(app) as client:
            r = client.get("/me/blocked-domains", headers=_auth(token))
        assert r.status_code == 200
        assert r.json() == []
