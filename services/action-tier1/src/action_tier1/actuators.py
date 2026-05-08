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


_LOCAL_ALLOW_LIST_HITS = counter(
    "action_tier1_sinkhole_local_allow_list_hits_total",
    "Sinkhole calls suppressed by the local actuator-side allow-list.",
    labelnames=("matched",),
)


class SinkholeApiClient:
    """Thin HTTP client for a DNS sinkhole / RPZ feed-management API.

    The shape mirrors common DNS sinkhole vendor APIs (BIND RPZ feed
    management, DNSDist HTTP API, Pi-hole gravity-add). We POST a small
    JSON document; vendor adapters live behind environment overrides.

    The dev stub adapter — a `httpbin`-style echo or a docker compose
    sinkhole stub — speaks the same wire format, so production wiring
    is just a URL change.
    """

    def __init__(self, *, base_url: str, timeout_s: float, token: str | None) -> None:
        self._base_url = base_url
        self._timeout = timeout_s
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}

    async def add(
        self,
        *,
        domain: str,
        decision_id: str,
        policy_version: str,
        category: str = "phishing",
    ) -> tuple[int, dict[str, object]]:
        """POST to the sinkhole. Returns (status_code, body)."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                self._base_url,
                json={
                    "domain": domain,
                    "category": category,
                    "decision_id": decision_id,
                    "policy_version": policy_version,
                    "ttl_seconds": 86_400,
                },
                headers=self._headers,
            )
            try:
                body = resp.json()
            except ValueError:
                body = {}
            return resp.status_code, body


def _is_locally_allow_listed(domain: str, allow_list: frozenset[str]) -> bool:
    """Belt-and-suspenders allow-list check at the actuator boundary.

    Even if url-intel were misconfigured or out of sync, we never sinkhole
    an MTN-owned or critical-services domain. Match is exact or as a
    registrable suffix (login.mtn.com.gh ↦ matches mtn.com.gh).
    """
    d = domain.strip().lower().rstrip(".")
    if not d:
        return False
    if d in allow_list:
        return True
    labels = d.split(".")
    for i in range(1, len(labels)):
        if ".".join(labels[i:]) in allow_list:
            return True
    return False


class DnsSinkholeActuator(Actuator):
    """Two-step: register the domain at url-intel, then push to the DNS sinkhole.

    Order of defence:
      1. Local allow-list short-circuit at the actuator (defensive).
      2. url-intel `/blocklist/add` (authoritative allow-list + dedup).
      3. SinkholeApiClient.add() against the resolver-side block API.
      4. Both must succeed; any failure marks the action `failed`.

    The sinkhole client is injected so tests can pass a stub speaking the
    real wire format. In dev (`sinkhole_url` empty) we register at url-intel
    only and rely on the resolver's pull-side `/blocklist/export`.
    """

    def __init__(
        self,
        *,
        action: str,
        url_intel_url: str,
        sinkhole_url: str,
        actuator_id: str,
        timeout_s: float = 0.1,
        token: str | None = None,
        local_allow_list: frozenset[str] = frozenset(),
        sinkhole_client: SinkholeApiClient | None = None,
    ) -> None:
        self.action = action
        self.actuator_id = actuator_id
        self._url_intel = url_intel_url.rstrip("/")
        self._sinkhole_url = sinkhole_url
        self._timeout = timeout_s
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._local_allow_list = local_allow_list
        if sinkhole_client is None and sinkhole_url:
            sinkhole_client = SinkholeApiClient(
                base_url=sinkhole_url, timeout_s=timeout_s, token=token
            )
        self._sinkhole_client = sinkhole_client

    async def execute(self, decision: DecisionDispatchedV1) -> ActuationResult:
        if decision.subject.kind.value != "url":
            return ActuationResult(
                outcome="failed", actuator_id=self.actuator_id, error="not a url subject"
            )
        domain = decision.subject.id

        # 1. Local defensive allow-list. Logs WARN so an alert can fire if
        # we ever see this — it indicates url-intel and the local list have
        # drifted enough that a real block would have hit a critical domain.
        if _is_locally_allow_listed(domain, self._local_allow_list):
            _LOCAL_ALLOW_LIST_HITS.labels(matched="true").inc()
            _INVOCATIONS.labels(action=self.action, outcome="suppressed").inc()
            _log.warning(
                "tier1.sinkhole.local_allow_list_hit",
                domain=domain,
                decision_id=decision.decision_id,
            )
            return ActuationResult(
                outcome="suppressed",
                actuator_id=self.actuator_id,
                error="local_allow_listed",
            )

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                # 2. Register with url-intel (authoritative allow-list-aware).
                ui_resp = await client.post(
                    f"{self._url_intel}/blocklist/add",
                    json={
                        "domain": domain,
                        "source": f"action-tier1:{decision.decision_id}",
                        "category": "phishing",
                        "confidence": float(decision.score.value) if decision.score else 0.95,
                    },
                    headers=self._headers,
                )
                if ui_resp.status_code >= 400:
                    _INVOCATIONS.labels(action=self.action, outcome="failed").inc()
                    return ActuationResult(
                        outcome="failed",
                        actuator_id=self.actuator_id,
                        error=f"url-intel http {ui_resp.status_code}",
                    )
                body = ui_resp.json()
                if not body.get("added", False) and body.get("reason") == "allow_listed":
                    _INVOCATIONS.labels(action=self.action, outcome="suppressed").inc()
                    return ActuationResult(
                        outcome="suppressed", actuator_id=self.actuator_id, error="allow_listed"
                    )

                # 3. Push to the DNS sinkhole. Dev mode (no sinkhole_url) relies
                # on the resolver pulling /blocklist/export from url-intel.
                if self._sinkhole_client is None:
                    _INVOCATIONS.labels(action=self.action, outcome="executed").inc()
                    return ActuationResult(outcome="executed", actuator_id=self.actuator_id)

                status, _ = await self._sinkhole_client.add(
                    domain=domain,
                    decision_id=decision.decision_id,
                    policy_version=decision.policy_version,
                )
                if status >= 400:
                    _INVOCATIONS.labels(action=self.action, outcome="failed").inc()
                    return ActuationResult(
                        outcome="failed",
                        actuator_id=self.actuator_id,
                        error=f"sinkhole http {status}",
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
            return ActuationResult(outcome="failed", actuator_id=self.actuator_id, error=str(exc))


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


class OtpHoldActuator(_HttpActuator):
    """Hold an OTP-bearing SMS at the SMSC and notify the customer.

    Inline action for the `otp.hold_and_alert` decision: the upstream
    detector (brain-otp-guard) flagged that an OTP SMS arrived while the
    recipient was on a call. Two-step adapter call (single HTTP POST in
    Phase 1; SMSC integration is stubbed to NoopActuator until the SMSC
    contract is finalised — see runbook):

      1. Hold the SMS (delay delivery by `hold_duration_s`).
      2. Push a USSD prompt to the recipient asking to confirm release.

    Real SMSC wiring (vendor-specific) lands when the integration team
    delivers the OTA / SMSC adapter spec.
    """

    def __init__(
        self,
        *,
        action: str,
        url: str,
        actuator_id: str,
        timeout_s: float = 0.1,
        token: str | None = None,
        hold_duration_s: int = 60,
    ) -> None:
        super().__init__(
            action=action, url=url, actuator_id=actuator_id, timeout_s=timeout_s, token=token
        )
        self._hold_duration_s = hold_duration_s

    async def execute(self, decision: DecisionDispatchedV1) -> ActuationResult:
        if decision.subject.kind.value != "number":
            return ActuationResult(
                outcome="failed", actuator_id=self.actuator_id, error="not a number subject"
            )
        return await self._post(
            {
                "msisdn": decision.subject.id,
                "hold_duration_s": self._hold_duration_s,
                "prompt": "otp_fraud_warning",
                "caller": decision.metadata.get("caller", ""),
                "decision_id": decision.decision_id,
                "policy_version": decision.policy_version,
            }
        )


class ActuatorRegistry:
    """Maps decision.action → Actuator implementation."""

    def __init__(self, actuators: dict[str, Actuator]) -> None:
        self._actuators = actuators

    def get(self, action: str) -> Actuator | None:
        return self._actuators.get(action)
