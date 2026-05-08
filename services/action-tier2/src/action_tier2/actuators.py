"""Tier-2 NRT actuator adapters.

Same shape as action-tier1, but timeouts are looser (2 s vs 100 ms) and the
backend set is customer-facing rather than network-side. Customer-facing
actuators resolve the subscriber's preferred locale and include both the
locale code and the rendered message body in the payload — downstream
notifier (SMS gateway / push hub) does not need an i18n catalogue.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

import httpx

from fraudnet.i18n import DEFAULT_LOCALE, translate
from fraudnet.obs import counter, get_logger
from fraudnet.schemas.events import DecisionDispatchedV1
from action_tier2.locale import StaticLocaleResolver, SubscriberLocaleResolver

_log = get_logger("action_tier2.actuators")

_INVOCATIONS = counter(
    "action_tier2_invocations_total",
    "Tier-2 actuator invocations.",
    labelnames=("action", "outcome"),
)


Outcome = Literal["executed", "suppressed", "failed", "dry_run"]


@dataclass(frozen=True)
class ActuationResult:
    outcome: Outcome
    actuator_id: str
    error: str | None = None


class Actuator(ABC):
    action: str
    actuator_id: str

    @abstractmethod
    async def execute(self, decision: DecisionDispatchedV1) -> ActuationResult: ...


class NoopActuator(Actuator):
    def __init__(self, *, action: str) -> None:
        self.action = action
        self.actuator_id = f"noop:{action}"

    async def execute(self, decision: DecisionDispatchedV1) -> ActuationResult:
        _log.info(
            "tier2.dry_run",
            action=decision.action,
            decision_id=decision.decision_id,
        )
        _INVOCATIONS.labels(action=self.action, outcome="dry_run").inc()
        return ActuationResult(outcome="dry_run", actuator_id=self.actuator_id)


class _HttpActuator(Actuator):
    def __init__(
        self,
        *,
        action: str,
        url: str,
        actuator_id: str,
        timeout_s: float = 2.0,
        token: str | None = None,
        locale_resolver: SubscriberLocaleResolver | None = None,
    ) -> None:
        self.action = action
        self.actuator_id = actuator_id
        self._url = url
        self._timeout = timeout_s
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._locale_resolver = locale_resolver or StaticLocaleResolver()

    async def _resolve_locale(self, msisdn: str) -> str:
        try:
            return await self._locale_resolver.resolve(msisdn)
        except Exception as exc:  # noqa: BLE001 — locale lookup must not break notification
            _log.warning("tier2.locale_lookup_failed", msisdn=msisdn, error=str(exc))
            return DEFAULT_LOCALE

    async def _post(self, payload: dict[str, object]) -> ActuationResult:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(self._url, json=payload, headers=self._headers)
                if resp.status_code >= 400:
                    _INVOCATIONS.labels(action=self.action, outcome="failed").inc()
                    return ActuationResult(
                        outcome="failed",
                        actuator_id=self.actuator_id,
                        error=f"http {resp.status_code}",
                    )
                _INVOCATIONS.labels(action=self.action, outcome="executed").inc()
                return ActuationResult(outcome="executed", actuator_id=self.actuator_id)
        except httpx.TimeoutException:
            _INVOCATIONS.labels(action=self.action, outcome="failed").inc()
            return ActuationResult(
                outcome="failed", actuator_id=self.actuator_id, error="timeout"
            )
        except Exception as exc:  # noqa: BLE001
            _INVOCATIONS.labels(action=self.action, outcome="failed").inc()
            return ActuationResult(
                outcome="failed", actuator_id=self.actuator_id, error=str(exc)
            )


_ALERT_TEMPLATE_KEY: dict[str, str] = {
    "customer.alert_smishing": "spam_sms_warning",
    "customer.alert_spam_call": "spam_call_warning",
    "customer.alert_otp_fraud": "otp_fraud_warning",
    "customer.alert_url_blocked": "url_blocked",
    "customer.alert_fraud": "fraud_alert",
}


class CustomerSmsAlertActuator(_HttpActuator):
    """Customer-facing SMS / push alert.

    Looks up the subscriber's preferred locale and renders the alert body
    using the i18n catalogue. The downstream notifier (SMS gateway / push)
    receives `locale` + `body` and does not need an i18n catalogue.
    """

    async def execute(self, decision: DecisionDispatchedV1) -> ActuationResult:
        if decision.subject.kind.value != "number":
            return ActuationResult(
                outcome="failed",
                actuator_id=self.actuator_id,
                error="not a number subject",
            )
        msisdn = decision.subject.id
        locale = await self._resolve_locale(msisdn)
        template_key = _ALERT_TEMPLATE_KEY.get(decision.action, "fraud_alert")
        body = translate(template_key, locale=locale)
        # The runner has already gated on protection-mode; here we only
        # surface the mode in the payload so the SMS gateway can choose
        # the right delivery channel (passive = SMS only; active may
        # also push via app/USSD).
        return await self._post(
            {
                "msisdn": msisdn,
                "alert_kind": decision.action,
                "decision_id": decision.decision_id,
                "severity": decision.severity.value,
                "locale": locale,
                "template_key": template_key,
                "body": body,
                "protection_mode": str(decision.metadata.get("protection_mode") or "passive"),
            }
        )


class DoIKnowYouPromptActuator(_HttpActuator):
    """In-app prompt asking the user to confirm an unfamiliar device / SIM."""

    async def execute(self, decision: DecisionDispatchedV1) -> ActuationResult:
        if decision.subject.kind.value != "number":
            return ActuationResult(
                outcome="failed",
                actuator_id=self.actuator_id,
                error="not a number subject",
            )
        msisdn = decision.subject.id
        locale = await self._resolve_locale(msisdn)
        recipient = str(decision.metadata.get("recipient") or msisdn)
        return await self._post(
            {
                "msisdn": msisdn,
                "prompt": "do_i_know_you",
                "decision_id": decision.decision_id,
                "locale": locale,
                "step1": translate("diky_step1", locale=locale, recipient=recipient),
                "step2": translate(
                    "diky_step2",
                    locale=locale,
                    amount=str(decision.metadata.get("amount") or "?"),
                ),
                "step3": translate("diky_step3", locale=locale),
            }
        )


class MoMoReviewLimitActuator(_HttpActuator):
    """Request a manual review of a wallet's transaction limit."""

    async def execute(self, decision: DecisionDispatchedV1) -> ActuationResult:
        if decision.subject.kind.value != "wallet":
            return ActuationResult(
                outcome="failed",
                actuator_id=self.actuator_id,
                error="not a wallet subject",
            )
        return await self._post(
            {
                "wallet_id": decision.subject.id,
                "review_kind": "tx_limit",
                "decision_id": decision.decision_id,
                "severity": decision.severity.value,
            }
        )


class SafeguardEnrollActuator(_HttpActuator):
    """Auto-enroll a customer in SafeGuard."""

    async def execute(self, decision: DecisionDispatchedV1) -> ActuationResult:
        if decision.subject.kind.value not in {"number", "wallet"}:
            return ActuationResult(
                outcome="failed",
                actuator_id=self.actuator_id,
                error="unsupported subject kind",
            )
        return await self._post(
            {
                "subject_kind": decision.subject.kind.value,
                "subject_id": decision.subject.id,
                "decision_id": decision.decision_id,
                "auto_enroll": True,
            }
        )


class ActuatorRegistry:
    def __init__(self, actuators: dict[str, Actuator]) -> None:
        self._actuators = actuators

    def get(self, action: str) -> Actuator | None:
        return self._actuators.get(action)
