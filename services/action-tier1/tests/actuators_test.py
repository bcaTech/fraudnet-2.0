from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from fraudnet.schemas.events import DecisionDispatchedV1
from fraudnet.schemas.types import EntityKind, LatencyTier, Severity, Subject
from action_tier1.actuators import (
    ActuatorRegistry,
    NoopActuator,
    OtpHoldActuator,
    UrlBlockActuator,
    VolteTagActuator,
)


def _decision(**overrides: object) -> DecisionDispatchedV1:
    base: dict[str, object] = {
        "event_id": "dec_t",
        "event_ts_ms": 1_700_000_000_000,
        "ingest_ts_ms": 1_700_000_000_000,
        "source": "decisions:test",
        "decision_id": "dec_test_001",
        "tier": LatencyTier.TIER1_INLINE,
        "action": "volte.tag_suspected_spam",
        "subject": Subject(kind=EntityKind.NUMBER, id="+233241234567"),
        "severity": Severity.HIGH,
        "policy_id": "default",
        "policy_version": "1",
    }
    base.update(overrides)
    return DecisionDispatchedV1.model_validate(base)


class TestNoopActuator:
    async def test_returns_dry_run(self) -> None:
        a = NoopActuator(action="x.y")
        result = await a.execute(_decision())
        assert result.outcome == "dry_run"
        assert result.actuator_id.startswith("noop:")


class TestVolteTagActuator:
    async def test_rejects_non_number_subject(self) -> None:
        a = VolteTagActuator(
            action="volte.tag_suspected_spam",
            url="http://ims.example/tag",
            actuator_id="ims",
        )
        result = await a.execute(_decision(subject=Subject(kind=EntityKind.WALLET, id="W:1")))
        assert result.outcome == "failed"
        assert result.error == "not a number subject"

    async def test_calls_backend_and_returns_executed(self) -> None:
        captured: dict[str, object] = {}

        def _handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["payload"] = request.content
            return httpx.Response(200, json={})

        transport = httpx.MockTransport(_handler)
        with patch("httpx.AsyncClient", lambda *a, **k: httpx.AsyncClient(transport=transport, **k)):
            a = VolteTagActuator(
                action="volte.tag_suspected_spam",
                url="http://ims.example/tag",
                actuator_id="ims",
            )
            result = await a.execute(_decision())
            assert result.outcome == "executed"

    async def test_500_returns_failed(self) -> None:
        def _handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="bad gateway")

        transport = httpx.MockTransport(_handler)
        with patch("httpx.AsyncClient", lambda *a, **k: httpx.AsyncClient(transport=transport, **k)):
            a = VolteTagActuator(
                action="volte.tag_suspected_spam",
                url="http://ims.example/tag",
                actuator_id="ims",
            )
            result = await a.execute(_decision())
            assert result.outcome == "failed"
            assert "503" in (result.error or "")


class TestUrlBlockActuator:
    async def test_rejects_non_url_subject(self) -> None:
        a = UrlBlockActuator(
            action="url.block", url="http://dns.example/block", actuator_id="dns"
        )
        result = await a.execute(_decision())  # number subject
        assert result.outcome == "failed"


class TestOtpHoldActuator:
    async def test_rejects_non_number_subject(self) -> None:
        a = OtpHoldActuator(
            action="otp.hold_and_alert",
            url="http://smsc.example/hold",
            actuator_id="smsc-hold",
        )
        result = await a.execute(
            _decision(subject=Subject(kind=EntityKind.WALLET, id="W:1"))
        )
        assert result.outcome == "failed"
        assert result.error == "not a number subject"

    async def test_posts_hold_request_with_caller_metadata(self) -> None:
        captured: dict[str, object] = {}

        def _handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = request.content
            return httpx.Response(200, json={"held": True})

        transport = httpx.MockTransport(_handler)
        with patch("httpx.AsyncClient", lambda *a, **k: httpx.AsyncClient(transport=transport, **k)):
            a = OtpHoldActuator(
                action="otp.hold_and_alert",
                url="http://smsc.example/hold",
                actuator_id="smsc-hold",
                hold_duration_s=90,
            )
            result = await a.execute(
                _decision(metadata={"caller": "+233207777777", "rule_id": "otp-during-call-tier1"})
            )
        assert result.outcome == "executed"
        body = captured["payload"]
        assert isinstance(body, (bytes, bytearray))
        decoded = body.decode()
        assert "+233207777777" in decoded
        assert "90" in decoded


class TestRegistry:
    def test_returns_none_for_unknown_action(self) -> None:
        reg = ActuatorRegistry({})
        assert reg.get("nope") is None

    def test_returns_actuator_for_known_action(self) -> None:
        a = NoopActuator(action="x")
        reg = ActuatorRegistry({"x": a})
        assert reg.get("x") is a
