"""Authenticated principal — what we've decided about the caller after auth."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Role(StrEnum):
    """The closed set of FraudNet roles. New roles require security sign-off.

    Mirrors Keycloak realm roles. Display labels live in the frontend; this is
    the authorisation surface.
    """

    # NOC investigator workbench
    FRAUD_ANALYST = "FRAUD_ANALYST"
    FRAUD_LEAD = "FRAUD_LEAD"
    FRAUD_MANAGER = "FRAUD_MANAGER"
    NOC_VIEWER = "NOC_VIEWER"

    # Customer self-service
    CUSTOMER = "CUSTOMER"

    # B2B enterprise (Phase 4)
    ENTERPRISE_ADMIN = "ENTERPRISE_ADMIN"
    ENTERPRISE_USER = "ENTERPRISE_USER"

    # System administration
    SYSTEM_ADMIN = "SYSTEM_ADMIN"
    AUDITOR = "AUDITOR"

    # Internal service identity (workload identity → role mapping)
    SERVICE = "SERVICE"


@dataclass(frozen=True)
class Principal:
    subject: str
    actor_kind: str  # 'user' | 'service' | 'system'
    roles: frozenset[Role]
    tenant_id: str
    step_up_at_ms: int | None = None  # epoch ms when step-up token was issued
    claims: dict[str, object] = field(default_factory=dict)

    def has_role(self, role: Role) -> bool:
        return role in self.roles

    def has_any(self, *roles: Role) -> bool:
        return bool(self.roles.intersection(roles))

    def has_step_up(self, *, max_age_ms: int = 300_000) -> bool:
        """True if step-up auth happened within `max_age_ms` (default 5 min)."""
        if self.step_up_at_ms is None:
            return False
        from time import time

        return (int(time() * 1000) - self.step_up_at_ms) <= max_age_ms
