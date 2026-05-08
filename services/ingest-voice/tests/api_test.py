from __future__ import annotations

import hmac
import json
from typing import cast

import pytest
from fastapi.testclient import TestClient

from fraudnet.schemas.events import VoiceEventV1
from ingest_voice.deps import IngestDeps
from ingest_voice.idempotency import InMemoryIdempotencyCache
from ingest_voice.main import create_app
from ingest_voice.settings import Settings


class _FakeProducer:
    def __init__(self) -> None:
        self.sent: list[tuple[VoiceEventV1, str | None]] = []
        self.fail_next = False

    async def start(self) -> None: return None

    async def send(self, payload: VoiceEventV1, *, key: str | None = None) -> None:
        if self.fail_next:
            from fraudnet.kafka.errors import DeliveryError

            self.fail_next = False
            raise DeliveryError("fake", topic=payload.topic, key=key)
        self.sent.append((payload, key))

    async def flush(self, timeout: float = 30.0) -> int:  # noqa: ARG002
        return 0

    async def stop(self) -> None: return None


@pytest.fixture
def deps_and_producer() -> tuple[IngestDeps, _FakeProducer]:
    settings = Settings(webhook_shared_secret="dev-secret", vendor_id="probe-test")
    producer = _FakeProducer()
    deps = IngestDeps(
        settings=settings,
        producer=cast(object, producer),  # type: ignore[arg-type]
        idempotency=InMemoryIdempotencyCache(),
    )
    return deps, producer


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "cdr_id": "CDR-T1",
        "event_type": "CALL_START",
        "timestamp_ms": 1_700_000_000_000,
        "caller": "0241234567",
        "callee": "0207654321",
        "duration_s": 0,
        "network": "VoLTE",
    }
    base.update(overrides)
    return base


def _sign(body: bytes, secret: str = "dev-secret") -> dict[str, str]:
    return {"X-Probe-Signature": hmac.new(secret.encode(), body, "sha256").hexdigest()}


class TestVoiceWebhook:
    def test_happy_path(self, deps_and_producer: tuple[IngestDeps, _FakeProducer]) -> None:
        deps, producer = deps_and_producer
        app = create_app(deps=deps)
        body = json.dumps(_payload()).encode()
        with TestClient(app) as client:
            r = client.post("/probe/voice", content=body, headers=_sign(body))
        assert r.status_code == 202
        assert r.json()["status"] == "accepted"
        assert producer.sent[0][1] == "+233241234567"  # partition key = caller

    def test_bad_signature_401(self, deps_and_producer: tuple[IngestDeps, _FakeProducer]) -> None:
        deps, producer = deps_and_producer
        app = create_app(deps=deps)
        body = json.dumps(_payload()).encode()
        with TestClient(app) as client:
            r = client.post("/probe/voice", content=body, headers={"X-Probe-Signature": "x"})
        assert r.status_code == 401
        assert producer.sent == []

    def test_duplicate_suppressed(self, deps_and_producer: tuple[IngestDeps, _FakeProducer]) -> None:
        deps, producer = deps_and_producer
        app = create_app(deps=deps)
        body = json.dumps(_payload()).encode()
        with TestClient(app) as client:
            r1 = client.post("/probe/voice", content=body, headers=_sign(body))
            r2 = client.post("/probe/voice", content=body, headers=_sign(body))
        assert r1.json()["status"] == "accepted"
        assert r2.json()["status"] == "duplicate"
        assert len(producer.sent) == 1

    def test_unknown_kind_400(self, deps_and_producer: tuple[IngestDeps, _FakeProducer]) -> None:
        deps, _ = deps_and_producer
        app = create_app(deps=deps)
        body = json.dumps(_payload(event_type="MYSTERY")).encode()
        with TestClient(app) as client:
            r = client.post("/probe/voice", content=body, headers=_sign(body))
        assert r.status_code == 400

    def test_kafka_failure_503(self, deps_and_producer: tuple[IngestDeps, _FakeProducer]) -> None:
        deps, producer = deps_and_producer
        producer.fail_next = True
        app = create_app(deps=deps)
        body = json.dumps(_payload()).encode()
        with TestClient(app) as client:
            r = client.post("/probe/voice", content=body, headers=_sign(body))
        assert r.status_code == 503

    def test_health_endpoints(self, deps_and_producer: tuple[IngestDeps, _FakeProducer]) -> None:
        deps, _ = deps_and_producer
        app = create_app(deps=deps)
        with TestClient(app) as client:
            assert client.get("/health/live").status_code == 200
            assert client.get("/health/ready").status_code == 200
