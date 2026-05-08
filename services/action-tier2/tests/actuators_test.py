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


import json

from action_tier2.locale import MappingLocaleResolver


async def test_customer_alert_localises_payload() -> None:
    captured: dict[str, object] = {}

    def _handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = req.content
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(_handler)
    with patch("httpx.AsyncClient", lambda *a, **k: httpx.AsyncClient(transport=transport, **k)):
        resolver = MappingLocaleResolver(mapping={"+233241234567": "tw"})
        a = CustomerSmsAlertActuator(
            action="customer.alert_smishing",
            url="http://notify.example/alert",
            actuator_id="notify",
            locale_resolver=resolver,
        )
        r = await a.execute(
            _decision("customer.alert_smishing", EntityKind.NUMBER, "+233241234567")
        )
        assert r.outcome == "executed"
        body = json.loads(captured["body"])
        assert body["locale"] == "tw"
        assert body["template_key"] == "spam_sms_warning"
        assert body["body"]
        assert body["body"] != ""


async def test_customer_alert_falls_back_to_english_for_unmapped_msisdn() -> None:
    captured: dict[str, object] = {}

    def _handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = req.content
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(_handler)
    with patch("httpx.AsyncClient", lambda *a, **k: httpx.AsyncClient(transport=transport, **k)):
        resolver = MappingLocaleResolver(mapping={"+233241111111": "ha"}, default="en")
        a = CustomerSmsAlertActuator(
            action="customer.alert_smishing",
            url="http://notify.example/alert",
            actuator_id="notify",
            locale_resolver=resolver,
        )
        await a.execute(_decision("customer.alert_smishing", EntityKind.NUMBER, "+233242222222"))
        body = json.loads(captured["body"])
        assert body["locale"] == "en"


from action_tier2.protection import (
    ACTIVE_MODE_ONLY,
    HIGH_SEVERITY_ONLY,
    PASSIVE_AUTO_ENROLLED,
    is_action_allowed,
)


class TestProtectionGate:
    def test_active_mode_allows_everything(self) -> None:
        for action in (
            "customer.alert_smishing",
            "customer.alert_fraud",
            "customer.do_i_know_you_prompt",
            "customer.ask_me_first",
        ):
            assert is_action_allowed(action, mode="active", severity="low") is True

    def test_passive_allows_auto_enrolled(self) -> None:
        for action in PASSIVE_AUTO_ENROLLED:
            assert is_action_allowed(action, mode="passive", severity="low") is True

    def test_passive_blocks_active_only(self) -> None:
        for action in ACTIVE_MODE_ONLY:
            assert is_action_allowed(action, mode="passive", severity="critical") is False

    def test_passive_high_severity_only_gates_on_severity(self) -> None:
        for action in HIGH_SEVERITY_ONLY:
            assert is_action_allowed(action, mode="passive", severity="critical") is True
            assert is_action_allowed(action, mode="passive", severity="high") is True
            assert is_action_allowed(action, mode="passive", severity="medium") is False
            assert is_action_allowed(action, mode="passive", severity="low") is False

    def test_unrelated_actions_pass(self) -> None:
        # Wallet / non-customer-facing actions should pass through.
        assert is_action_allowed("momo.review_wallet_kyc", mode="passive", severity="low") is True
