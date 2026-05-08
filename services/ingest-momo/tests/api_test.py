"""Webhook end-to-end tests with a fake Kafka producer.

These run without Docker; for full integration against real Kafka + Schema
Registry, see api_integration_test.py (marked @pytest.mark.integration).
"""

from __future__ import annotations

import hmac
import json
from typing import cast

import pytest
from fastapi.testclient import TestClient

from fraudnet.schemas.events import MoMoEventV1
from ingest_momo.deps import IngestDeps
from ingest_momo.idempotency import InMemoryIdempotencyCache
from ingest_momo.main import create_app
from ingest_momo.settings import Settings


class _FakeProducer:
    """Stands in for AvroProducer[MoMoEventV1] in tests."""

    def __init__(self) -> None:
        self.sent: list[tuple[MoMoEventV1, str | None]] = []
        self.fail_next = False

    async def start(self) -> None:
        return None

    async def send(self, payload: MoMoEventV1, *, key: str | None = None) -> None:
        if self.fail_next:
            from fraudnet.kafka.errors import DeliveryError

            self.fail_next = False
            raise DeliveryError("fake failure", topic=payload.topic, key=key)
        self.sent.append((payload, key))

    async def flush(self, timeout: float = 30.0) -> int:  # noqa: ARG002
        return 0

    async def stop(self) -> None:
        return None


@pytest.fixture
def deps_and_producer() -> tuple[IngestDeps, _FakeProducer]:
    settings = Settings(webhook_shared_secret="dev-secret")
    producer = _FakeProducer()
    idem = InMemoryIdempotencyCache()
    deps = IngestDeps(
        settings=settings,
        producer=cast(object, producer),  # type: ignore[arg-type]
        idempotency=idem,
    )
    return deps, producer


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "txn_id": "MTN-MOMO-T1",
        "event_type": "P2P",
        "timestamp_ms": 1_700_000_000_000,
        "sender_wallet_id": "W:233241234567",
        "recipient_wallet_id": "W:233207654321",
        "sender_msisdn": "0241234567",
        "recipient_msisdn": "0207654321",
        "amount_minor": 5000,
        "currency": "GHS",
        "counterparty_kind": "wallet",
    }
    base.update(overrides)
    return base


def _sign(body: bytes, secret: str = "dev-secret") -> dict[str, str]:
    sig = hmac.new(secret.encode(), body, "sha256").hexdigest()
    return {"X-MoMo-Signature": sig}


class TestWebhook:
    def test_happy_path(
        self, deps_and_producer: tuple[IngestDeps, _FakeProducer]
    ) -> None:
        deps, producer = deps_and_producer
        app = create_app(deps=deps)
        body = json.dumps(_payload()).encode()
        with TestClient(app) as client:
            r = client.post("/webhooks/momo", content=body, headers=_sign(body))
        assert r.status_code == 202
        assert r.json()["status"] == "accepted"
        assert len(producer.sent) == 1
        sent_event, sent_key = producer.sent[0]
        assert sent_event.amount_minor == 5000
        assert sent_key == "W:233241234567"

    def test_rejects_bad_signature(
        self, deps_and_producer: tuple[IngestDeps, _FakeProducer]
    ) -> None:
        deps, producer = deps_and_producer
        app = create_app(deps=deps)
        body = json.dumps(_payload()).encode()
        with TestClient(app) as client:
            r = client.post(
                "/webhooks/momo",
                content=body,
                headers={"X-MoMo-Signature": "deadbeef"},
            )
        assert r.status_code == 401
        assert producer.sent == []

    def test_duplicate_returns_duplicate(
        self, deps_and_producer: tuple[IngestDeps, _FakeProducer]
    ) -> None:
        deps, producer = deps_and_producer
        app = create_app(deps=deps)
        body = json.dumps(_payload()).encode()
        with TestClient(app) as client:
            r1 = client.post("/webhooks/momo", content=body, headers=_sign(body))
            r2 = client.post("/webhooks/momo", content=body, headers=_sign(body))
        assert r1.status_code == 202 and r1.json()["status"] == "accepted"
        assert r2.status_code == 202 and r2.json()["status"] == "duplicate"
        assert len(producer.sent) == 1

    def test_unknown_event_type_400(
        self, deps_and_producer: tuple[IngestDeps, _FakeProducer]
    ) -> None:
        deps, producer = deps_and_producer
        app = create_app(deps=deps)
        body = json.dumps(_payload(event_type="MYSTERY")).encode()
        with TestClient(app) as client:
            r = client.post("/webhooks/momo", content=body, headers=_sign(body))
        assert r.status_code == 400
        assert producer.sent == []

    def test_invalid_msisdn_400(
        self, deps_and_producer: tuple[IngestDeps, _FakeProducer]
    ) -> None:
        deps, producer = deps_and_producer
        app = create_app(deps=deps)
        body = json.dumps(_payload(sender_msisdn="not-a-number")).encode()
        with TestClient(app) as client:
            r = client.post("/webhooks/momo", content=body, headers=_sign(body))
        assert r.status_code == 400
        assert producer.sent == []

    def test_kafka_failure_503(
        self, deps_and_producer: tuple[IngestDeps, _FakeProducer]
    ) -> None:
        deps, producer = deps_and_producer
        producer.fail_next = True
        app = create_app(deps=deps)
        body = json.dumps(_payload()).encode()
        with TestClient(app) as client:
            r = client.post("/webhooks/momo", content=body, headers=_sign(body))
        assert r.status_code == 503

    def test_health_live(
        self, deps_and_producer: tuple[IngestDeps, _FakeProducer]
    ) -> None:
        deps, _ = deps_and_producer
        app = create_app(deps=deps)
        with TestClient(app) as client:
            r = client.get("/health/live")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_health_ready(
        self, deps_and_producer: tuple[IngestDeps, _FakeProducer]
    ) -> None:
        deps, _ = deps_and_producer
        app = create_app(deps=deps)
        with TestClient(app) as client:
            r = client.get("/health/ready")
        assert r.status_code == 200
        assert r.json()["status"] == "ready"

    def test_request_id_propagates(
        self, deps_and_producer: tuple[IngestDeps, _FakeProducer]
    ) -> None:
        deps, _ = deps_and_producer
        app = create_app(deps=deps)
        with TestClient(app) as client:
            r = client.get("/health/live", headers={"x-request-id": "rid-test"})
        assert r.headers["x-request-id"] == "rid-test"
