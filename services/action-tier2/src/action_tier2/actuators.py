"""Tier-2 NRT actuator adapters.

Same shape as action-tier1, but timeouts are looser (2 s vs 100 ms) and the
backend set is customer-facing rather than network-side.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

import httpx

from fraudnet.obs import counter, get_logger
from fraudnet.schemas.events import DecisionDispatchedV1

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
    ) -> None:
        self.action = action
        self.actuator_id = actuator_id
        self._url = url
        self._timeout = timeout_s
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}

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


class CustomerSmsAlertActuator(_HttpActuator):
    """Customer-facing SMS / push alert."""

    async def execute(self, decision: DecisionDispatchedV1) -> ActuationResult:
        if decision.subject.kind.value != "number":
            return ActuationResult(
                outcome="failed",
                actuator_id=self.actuator_id,
                error="not a number subject",
            )
        return await self._post(
            {
                "msisdn": decision.subject.id,
                "alert_kind": decision.action,
                "decision_id": decision.decision_id,
                "severity": decision.severity.value,
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
        return await self._post(
            {
                "msisdn": decision.subject.id,
                "prompt": "do_i_know_you",
                "decision_id": decision.decision_id,
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
