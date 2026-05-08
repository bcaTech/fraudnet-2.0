from __future__ import annotations

from unittest.mock import patch

import httpx

from fraudnet.schemas.events import DecisionDispatchedV1
from fraudnet.schemas.types import EntityKind, LatencyTier, Severity, Subject
from action_tier2.actuators import (
    CustomerSmsAlertActuator,
    DoIKnowYouPromptActuator,
    MoMoReviewLimitActuator,
    NoopActuator,
    SafeguardEnrollActuator,
)


def _decision(action: str, subject_kind: EntityKind, subject_id: str) -> DecisionDispatchedV1:
    return DecisionDispatchedV1.model_validate(
        {
            "event_id": "dec_t",
            "event_ts_ms": 1_700_000_000_000,
            "ingest_ts_ms": 1_700_000_000_000,
            "source": "decisions:test",
            "decision_id": "dec_test_002",
            "tier": LatencyTier.TIER2_NRT,
            "action": action,
            "subject": Subject(kind=subject_kind, id=subject_id),
            "severity": Severity.MEDIUM,
            "policy_id": "default",
            "policy_version": "1",
        }
    )


async def test_noop_returns_dry_run() -> None:
    a = NoopActuator(action="t.x")
    r = await a.execute(_decision("t.x", EntityKind.NUMBER, "+233241234567"))
    assert r.outcome == "dry_run"


async def test_customer_alert_calls_backend() -> None:
    captured: dict[str, object] = {}

    def _handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["body"] = req.content
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(_handler)
    with patch("httpx.AsyncClient", lambda *a, **k: httpx.AsyncClient(transport=transport, **k)):
        a = CustomerSmsAlertActuator(
            action="customer.alert_smishing",
            url="http://notify.example/alert",
            actuator_id="notify",
        )
        r = await a.execute(_decision("customer.alert_smishing", EntityKind.NUMBER, "+233241234567"))
        assert r.outcome == "executed"


async def test_customer_alert_rejects_wallet_subject() -> None:
    a = CustomerSmsAlertActuator(
        action="customer.alert_smishing",
        url="http://notify.example/alert",
        actuator_id="notify",
    )
    r = await a.execute(_decision("customer.alert_smishing", EntityKind.WALLET, "W:1"))
    assert r.outcome == "failed"


async def test_momo_limit_rejects_number_subject() -> None:
    a = MoMoReviewLimitActuator(
        action="momo.review_limit",
        url="http://momo.example/limit",
        actuator_id="momo-limit",
    )
    r = await a.execute(_decision("momo.review_limit", EntityKind.NUMBER, "+233241234567"))
    assert r.outcome == "failed"


async def test_safeguard_accepts_number_or_wallet() -> None:
    def _handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(_handler)
    with patch("httpx.AsyncClient", lambda *a, **k: httpx.AsyncClient(transport=transport, **k)):
        a = SafeguardEnrollActuator(
            action="safeguard.enroll", url="http://sg.example/enroll", actuator_id="sg"
        )
        r1 = await a.execute(_decision("safeguard.enroll", EntityKind.NUMBER, "+233241234567"))
        r2 = await a.execute(_decision("safeguard.enroll", EntityKind.WALLET, "W:1"))
        assert r1.outcome == "executed"
        assert r2.outcome == "executed"


async def test_do_i_know_you_rejects_non_number() -> None:
    a = DoIKnowYouPromptActuator(
        action="customer.do_i_know_you_prompt",
        url="http://app.example/prompt",
        actuator_id="app",
    )
    r = await a.execute(_decision("customer.do_i_know_you_prompt", EntityKind.WALLET, "W:1"))
    assert r.outcome == "failed"
