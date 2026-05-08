from __future__ import annotations

import hmac
import json
from typing import cast

import pytest
from fastapi.testclient import TestClient

from fraudnet.schemas.events import SmsEventV1
from ingest_sms.deps import IngestDeps
from ingest_sms.idempotency import InMemoryIdempotencyCache
from ingest_sms.main import create_app
from ingest_sms.settings import Settings


class _FakeProducer:
    def __init__(self) -> None:
        self.sent: list[tuple[SmsEventV1, str | None]] = []

    async def start(self) -> None: return None

    async def send(self, payload: SmsEventV1, *, key: str | None = None) -> None:
        self.sent.append((payload, key))

    async def flush(self, timeout: float = 30.0) -> int:  # noqa: ARG002
        return 0

    async def stop(self) -> None: return None


@pytest.fixture
def deps_and_producer() -> tuple[IngestDeps, _FakeProducer]:
    settings = Settings(webhook_shared_secret="dev-secret", smsc_id="smsc-test")
    producer = _FakeProducer()
    return (
        IngestDeps(
            settings=settings,
            producer=cast(object, producer),  # type: ignore[arg-type]
            idempotency=InMemoryIdempotencyCache(),
        ),
        producer,
    )


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "smsc_msg_id": "MSG-1",
        "event_type": "MT",
        "timestamp_ms": 1_700_000_000_000,
        "sender": "0241234567",
        "recipient": "0207654321",
        "body": "Your verification code is 1234",
    }
    base.update(overrides)
    return base


def _sign(body: bytes, secret: str = "dev-secret") -> dict[str, str]:
    return {"X-SMSC-Signature": hmac.new(secret.encode(), body, "sha256").hexdigest()}


class TestSmsWebhook:
    def test_happy_path(self, deps_and_producer: tuple[IngestDeps, _FakeProducer]) -> None:
        deps, producer = deps_and_producer
        app = create_app(deps=deps)
        body = json.dumps(_payload()).encode()
        with TestClient(app) as client:
            r = client.post("/smsc/push", content=body, headers=_sign(body))
        assert r.status_code == 202
        sent = producer.sent[0][0]
        assert sent.body is None  # capture disabled by default
        assert sent.body_hash is not None
        assert producer.sent[0][1] == "+233241234567"

    def test_body_capture_when_enabled(
        self, deps_and_producer: tuple[IngestDeps, _FakeProducer]
    ) -> None:
        deps, producer = deps_and_producer
        deps.settings = Settings(
            webhook_shared_secret="dev-secret",
            smsc_id="smsc-test",
            allow_body_capture=True,
        )
        app = create_app(deps=deps)
        body = json.dumps(_payload()).encode()
        with TestClient(app) as client:
            r = client.post("/smsc/push", content=body, headers=_sign(body))
        assert r.status_code == 202
        sent = producer.sent[0][0]
        assert sent.body is not None and "verification code" in sent.body

    def test_bad_signature_401(
        self, deps_and_producer: tuple[IngestDeps, _FakeProducer]
    ) -> None:
        deps, producer = deps_and_producer
        app = create_app(deps=deps)
        body = json.dumps(_payload()).encode()
        with TestClient(app) as client:
            r = client.post("/smsc/push", content=body, headers={"X-SMSC-Signature": "no"})
        assert r.status_code == 401
        assert producer.sent == []

    def test_unknown_kind_400(
        self, deps_and_producer: tuple[IngestDeps, _FakeProducer]
    ) -> None:
        deps, _ = deps_and_producer
        app = create_app(deps=deps)
        body = json.dumps(_payload(event_type="MYSTERY")).encode()
        with TestClient(app) as client:
            r = client.post("/smsc/push", content=body, headers=_sign(body))
        assert r.status_code == 400

    def test_health_live(
        self, deps_and_producer: tuple[IngestDeps, _FakeProducer]
    ) -> None:
        deps, _ = deps_and_producer
        app = create_app(deps=deps)
        with TestClient(app) as client:
            assert client.get("/health/live").status_code == 200
