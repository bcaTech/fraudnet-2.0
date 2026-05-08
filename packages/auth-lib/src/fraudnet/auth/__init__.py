"""JWT validation, RBAC, tenant scoping, step-up auth.

CLAUDE.md §7.1:
  - All user-facing API traffic uses JWT bearer tokens issued by Keycloak.
    Short-lived (5 min); refreshed via the gateway.
  - RBAC is enforced at the route level via `@require_role('FRAUD_LEAD')`.
  - Tenant scoping is enforced at the data layer (Postgres RLS).
  - Step-up auth uses a separate short-lived token obtained via WebAuthn /
    second factor for sensitive ops (model promotion, role changes, data
    export, takedown filing).
"""

from fraudnet.auth.principal import Principal, Role
from fraudnet.auth.rbac import require_role, require_step_up
from fraudnet.auth.token import (
    JwksCache,
    TokenError,
    TokenValidator,
    decode_token,
    extract_principal,
)

__all__ = [
    "JwksCache",
    "Principal",
    "Role",
    "TokenError",
    "TokenValidator",
    "decode_token",
    "extract_principal",
    "require_role",
    "require_step_up",
]
