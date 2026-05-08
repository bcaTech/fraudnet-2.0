"""Subscriber protection-mode resolution (DECISIONS.md D-008).

Every MTN subscriber is on `passive` mode by default — protected
automatically with SMS-only alerting. Subscribers who register on the
api-customer portal upgrade to `active` mode, which unlocks USSD/app
prompts and the full Tier-2 actuator set.

Mode is stored on the subscriber profile in production. For Phase 1
the resolver returns `passive` everywhere; tests inject a mapping
resolver to exercise the active-mode paths.
"""

from __future__ import annotations

from typing import Final, Literal, Protocol

ProtectionMode = Literal["passive", "active"]

DEFAULT_MODE: Final[ProtectionMode] = "passive"

# Action sets — kept in sync with `passive_protection.*` in the YAML
# policy. Duplication is deliberate: the policy block expresses *intent*
# (and is the source of truth for analyst review); the constants here
# are the actuator-side enforcement.
PASSIVE_AUTO_ENROLLED: Final[frozenset[str]] = frozenset(
    {
        "customer.alert_smishing",
        "customer.alert_spam_call",
        "customer.alert_otp_fraud",
        "customer.alert_url_blocked",
    }
)
HIGH_SEVERITY_ONLY: Final[frozenset[str]] = frozenset(
    {
        "customer.alert_fraud",
    }
)
ACTIVE_MODE_ONLY: Final[frozenset[str]] = frozenset(
    {
        "customer.do_i_know_you_prompt",
        "customer.ask_me_first",
        "momo.review_limit",
        "safeguard.enroll",
    }
)


class ProtectionModeResolver(Protocol):
    async def resolve(self, msisdn: str) -> ProtectionMode: ...


class StaticProtectionModeResolver:
    """Returns the configured default mode for every subscriber."""

    def __init__(self, *, default: ProtectionMode = DEFAULT_MODE) -> None:
        self._default = default

    async def resolve(self, msisdn: str) -> ProtectionMode:
        return self._default


class MappingProtectionModeResolver:
    """Test fixture — explicit msisdn → mode mapping."""

    def __init__(
        self,
        *,
        mapping: dict[str, ProtectionMode] | None = None,
        default: ProtectionMode = DEFAULT_MODE,
    ) -> None:
        self._mapping = mapping or {}
        self._default = default

    async def resolve(self, msisdn: str) -> ProtectionMode:
        return self._mapping.get(msisdn, self._default)


def is_action_allowed(
    action: str, *, mode: ProtectionMode, severity: str
) -> bool:
    """Decide whether to actuate `action` given the subscriber's mode.

    Rules (DECISIONS.md D-008):
      - Active subscribers see every customer-facing action.
      - Passive subscribers always see PASSIVE_AUTO_ENROLLED.
      - Passive subscribers see HIGH_SEVERITY_ONLY only at critical/high.
      - Passive subscribers do not see ACTIVE_MODE_ONLY.
      - Anything else (non-customer-facing actions) flows through.
    """
    if mode == "active":
        return True
    if action in PASSIVE_AUTO_ENROLLED:
        return True
    if action in HIGH_SEVERITY_ONLY:
        return severity in {"critical", "high"}
    if action in ACTIVE_MODE_ONLY:
        return False
    return True
