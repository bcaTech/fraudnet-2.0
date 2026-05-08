from __future__ import annotations

import hmac
import json
from typing import cast

import pytest
from fastapi.testclient import TestClient

from fraudnet.schemas.events import DataEventV1
from ingest_data.deps import IngestDeps
from ingest_data.idempotency import InMemoryIdempotencyCache
from ingest_data.main import create_app
from ingest_data.settings import Settings


class _FakeProducer:
    def __init__(self) -> None:
        self.sent: list[tuple[DataEventV1, str | None]] = []

    async def start(self) -> None: return None

    async def send(self, payload: DataEventV1, *, key: str | None = None) -> None:
        self.sent.append((payload, key))

    async def flush(self, timeout: float = 30.0) -> int:  # noqa: ARG002
        return 0

    async def stop(self) -> None: return None


@pytest.fixture
def deps_and_producer() -> tuple[IngestDeps, _FakeProducer]:
    settings = Settings(
        dns_webhook_shared_secret="dev-dns",
        ipdr_webhook_shared_secret="dev-ipdr",
        dns_resolver_id="res-test",
        ipdr_collector_id="ipdr-test",
    )
    producer = _FakeProducer()
    return (
        IngestDeps(
            settings=settings,
            producer=cast(object, producer),  # type: ignore[arg-type]
            idempotency=InMemoryIdempotencyCache(),
        ),
        producer,
    )


def _dns_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "query_id": "Q-1",
        "event_type": "QUERY",
        "timestamp_ms": 1_700_000_000_000,
        "msisdn": "0241234567",
        "qname": "login-momo.example.com",
        "qtype": "A",
    }
    base.update(overrides)
    return base


def _ipdr_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "session_id": "S-1",
        "timestamp_ms": 1_700_000_000_000,
        "msisdn": "0241234567",
        "dst_domain": "cdn.example.com",
        "bytes_up": 100,
        "bytes_down": 1_000,
    }
    base.update(overrides)
    return base


def _sign(body: bytes, secret: str) -> dict[str, str]:
    return {
        "X-DNS-Signature": hmac.new(secret.encode(), body, "sha256").hexdigest(),
        "X-IPDR-Signature": hmac.new(secret.encode(), body, "sha256").hexdigest(),
    }


class TestDnsWebhook:
    def test_happy_path(self, deps_and_producer: tuple[IngestDeps, _FakeProducer]) -> None:
        deps, producer = deps_and_producer
        app = create_app(deps=deps)
        body = json.dumps(_dns_payload()).encode()
        with TestClient(app) as client:
            r = client.post("/dns/push", content=body, headers=_sign(body, "dev-dns"))
        assert r.status_code == 202
        assert len(producer.sent) == 1
        sent, key = producer.sent[0]
        assert sent.kind == "dns_query"
        assert sent.msisdn == "+233241234567"
        assert key == "+233241234567"

    def test_unattributed_query_accepted(
        self, deps_and_producer: tuple[IngestDeps, _FakeProducer]
    ) -> None:
        deps, producer = deps_and_producer
        app = create_app(deps=deps)
        payload = _dns_payload()
        del payload["msisdn"]
        body = json.dumps(payload).encode()
        with TestClient(app) as client:
            r = client.post("/dns/push", content=body, headers=_sign(body, "dev-dns"))
        assert r.status_code == 202
        sent, key = producer.sent[0]
        assert sent.msisdn is None
        assert key == "d:login-momo.example.com"

    def test_bad_signature_401(
        self, deps_and_producer: tuple[IngestDeps, _FakeProducer]
    ) -> None:
        deps, producer = deps_and_producer
        app = create_app(deps=deps)
        body = json.dumps(_dns_payload()).encode()
        with TestClient(app) as client:
            r = client.post("/dns/push", content=body, headers={"X-DNS-Signature": "no"})
        assert r.status_code == 401
        assert producer.sent == []

    def test_unknown_kind_400(
        self, deps_and_producer: tuple[IngestDeps, _FakeProducer]
    ) -> None:
        deps, _ = deps_and_producer
        app = create_app(deps=deps)
        body = json.dumps(_dns_payload(event_type="MYSTERY")).encode()
        with TestClient(app) as client:
            r = client.post("/dns/push", content=body, headers=_sign(body, "dev-dns"))
        assert r.status_code == 400

    def test_idempotent_duplicate(
        self, deps_and_producer: tuple[IngestDeps, _FakeProducer]
    ) -> None:
        deps, producer = deps_and_producer
        app = create_app(deps=deps)
        body = json.dumps(_dns_payload()).encode()
        with TestClient(app) as client:
            r1 = client.post("/dns/push", content=body, headers=_sign(body, "dev-dns"))
            r2 = client.post("/dns/push", content=body, headers=_sign(body, "dev-dns"))
        assert r1.status_code == 202 and r2.status_code == 202
        assert r2.json()["status"] == "duplicate"
        assert len(producer.sent) == 1


class TestIpdrWebhook:
    def test_happy_path(self, deps_and_producer: tuple[IngestDeps, _FakeProducer]) -> None:
        deps, producer = deps_and_producer
        app = create_app(deps=deps)
        body = json.dumps(_ipdr_payload()).encode()
        with TestClient(app) as client:
            r = client.post("/ipdr/push", content=body, headers=_sign(body, "dev-ipdr"))
        assert r.status_code == 202
        sent, key = producer.sent[0]
        assert sent.kind == "ipdr_session"
        assert sent.bytes_up == 100
        assert sent.bytes_down == 1_000
        assert key == "+233241234567"

    def test_missing_destination_400(
        self, deps_and_producer: tuple[IngestDeps, _FakeProducer]
    ) -> None:
        deps, _ = deps_and_producer
        app = create_app(deps=deps)
        payload = _ipdr_payload()
        del payload["dst_domain"]
        body = json.dumps(payload).encode()
        with TestClient(app) as client:
            r = client.post("/ipdr/push", content=body, headers=_sign(body, "dev-ipdr"))
        assert r.status_code == 400

    def test_health_live(self, deps_and_producer: tuple[IngestDeps, _FakeProducer]) -> None:
        deps, _ = deps_and_producer
        app = create_app(deps=deps)
        with TestClient(app) as client:
            assert client.get("/health/live").status_code == 200
