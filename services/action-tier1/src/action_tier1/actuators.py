"""Tier-1 actuator adapters.

Each actuator is a small Protocol-style class with `execute(decision) -> Outcome`.
All HTTP backends use httpx with strict total-timeout < 100 ms so the inline
budget is preserved even on backend wobble. On timeout we mark the action
`failed` and let the upstream recovery (replay / SOC ticket) handle it.

Production wiring binds real backends via env-driven URLs. Dev defaults to
a NoopActuator that just logs the intended action.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

import httpx

from fraudnet.obs import counter, get_logger
from fraudnet.schemas.events import DecisionDispatchedV1

_log = get_logger("action_tier1.actuators")

_INVOCATIONS = counter(
    "action_tier1_invocations_total",
    "Tier-1 actuator invocations.",
    labelnames=("action", "outcome"),
)


Outcome = Literal["executed", "suppressed", "failed", "dry_run"]


@dataclass(frozen=True)
class ActuationResult:
    outcome: Outcome
    actuator_id: str
    error: str | None = None
    latency_ms: int | None = None


class Actuator(ABC):
    """Single-action actuator. Action name → adapter resolved by ActuatorRegistry."""

    action: str
    actuator_id: str

    @abstractmethod
    async def execute(self, decision: DecisionDispatchedV1) -> ActuationResult: ...


class NoopActuator(Actuator):
    """Dev / testing default. Logs the intended action; reports executed."""

    def __init__(self, *, action: str) -> None:
        self.action = action
        self.actuator_id = f"noop:{action}"

    async def execute(self, decision: DecisionDispatchedV1) -> ActuationResult:
        _log.info(
            "tier1.dry_run",
            action=decision.action,
            subject_kind=decision.subject.kind.value,
            subject_id=decision.subject.id,
            decision_id=decision.decision_id,
        )
        _INVOCATIONS.labels(action=self.action, outcome="dry_run").inc()
        return ActuationResult(outcome="dry_run", actuator_id=self.actuator_id)


class _HttpActuator(Actuator):
    """Common base for HTTP-backed actuators."""

    def __init__(
        self,
        *,
        action: str,
        url: str,
        actuator_id: str,
        timeout_s: float = 0.1,
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
                outcome="failed",
                actuator_id=self.actuator_id,
                error="timeout",
            )
        except Exception as exc:  # noqa: BLE001 — actuator failure must not crash runner
            _INVOCATIONS.labels(action=self.action, outcome="failed").inc()
            return ActuationResult(
                outcome="failed",
                actuator_id=self.actuator_id,
                error=str(exc),
            )


class VolteTagActuator(_HttpActuator):
    """SIP-header rewrite at the IMS core. Marks the call as Suspected SPAM."""

    async def execute(self, decision: DecisionDispatchedV1) -> ActuationResult:
        if decision.subject.kind.value != "number":
            return ActuationResult(outcome="failed", actuator_id=self.actuator_id, error="not a number subject")
        return await self._post(
            {
                "msisdn": decision.subject.id,
                "tag": "Suspected SPAM",
                "decision_id": decision.decision_id,
                "policy_version": decision.policy_version,
            }
        )


class UrlBlockActuator(_HttpActuator):
    """DNS sinkhole push for a matched URL."""

    async def execute(self, decision: DecisionDispatchedV1) -> ActuationResult:
        if decision.subject.kind.value != "url":
            return ActuationResult(outcome="failed", actuator_id=self.actuator_id, error="not a url subject")
        return await self._post(
            {
                "url": decision.subject.id,
                "decision_id": decision.decision_id,
                "policy_version": decision.policy_version,
            }
        )


class SmsBlockActuator(_HttpActuator):
    """Outbound SMS block at the SMSC."""

    async def execute(self, decision: DecisionDispatchedV1) -> ActuationResult:
        if decision.subject.kind.value != "number":
            return ActuationResult(outcome="failed", actuator_id=self.actuator_id, error="not a number subject")
        return await self._post(
            {
                "msisdn": decision.subject.id,
                "block_kind": "outbound_sms",
                "decision_id": decision.decision_id,
            }
        )


class MoMoSendWithCareActuator(_HttpActuator):
    """Inject a Send-with-Care prompt at the MoMo BSS."""

    async def execute(self, decision: DecisionDispatchedV1) -> ActuationResult:
        if decision.subject.kind.value != "wallet":
            return ActuationResult(outcome="failed", actuator_id=self.actuator_id, error="not a wallet subject")
        return await self._post(
            {
                "wallet_id": decision.subject.id,
                "prompt": "send_with_care",
                "decision_id": decision.decision_id,
            }
        )


class ActuatorRegistry:
    """Maps decision.action → Actuator implementation."""

    def __init__(self, actuators: dict[str, Actuator]) -> None:
        self._actuators = actuators

    def get(self, action: str) -> Actuator | None:
        return self._actuators.get(action)
