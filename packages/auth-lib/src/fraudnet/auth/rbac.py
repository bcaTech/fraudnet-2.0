"""RBAC decorators for FastAPI route handlers.

Usage:

    from fraudnet.auth import Role, require_role

    @router.post("/alerts/{alert_id}/claim")
    @require_role(Role.FRAUD_ANALYST, Role.FRAUD_LEAD)
    async def claim_alert(alert_id: UUID, principal: Principal = Depends(...)):
        ...

The decorator looks up the Principal from a fixed dependency name in the
route signature (`principal`). If the principal is missing or the role check
fails, raises ForbiddenError which the FastAPI exception handler maps to
the standard error envelope (CLAUDE.md §10.3).
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar

from fraudnet.auth.principal import Principal, Role
from fraudnet.schemas.errors import ForbiddenError, StepUpRequiredError

P = ParamSpec("P")
T = TypeVar("T")


def _extract_principal(args: tuple[object, ...], kwargs: dict[str, object]) -> Principal:
    pr = kwargs.get("principal")
    if isinstance(pr, Principal):
        return pr
    for a in args:
        if isinstance(a, Principal):
            return a
    raise ForbiddenError("no Principal in request scope — auth dependency missing")


def require_role(*roles: Role) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Require the caller to have at least one of the listed roles."""

    if not roles:
        raise ValueError("require_role: at least one role required")

    def decorator(fn: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        if not inspect.iscoroutinefunction(fn):
            raise TypeError("require_role only supports async route handlers")

        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            principal = _extract_principal(args, kwargs)
            if not principal.has_any(*roles):
                raise ForbiddenError(
                    f"role required: one of {[r.value for r in roles]}",
                    details={"required_any": [r.value for r in roles]},
                )
            return await fn(*args, **kwargs)

        return wrapper

    return decorator


def require_step_up(
    *,
    max_age_ms: int = 300_000,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Require a fresh step-up auth token (default within 5 minutes).

    Apply on top of `require_role` for sensitive ops: model promotion, role
    changes, data export, takedown filing (CLAUDE.md §7.1).
    """

    def decorator(fn: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        if not inspect.iscoroutinefunction(fn):
            raise TypeError("require_step_up only supports async route handlers")

        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            principal = _extract_principal(args, kwargs)
            if not principal.has_step_up(max_age_ms=max_age_ms):
                raise StepUpRequiredError(
                    "step-up authentication required",
                    details={"max_age_ms": max_age_ms},
                )
            return await fn(*args, **kwargs)

        return wrapper

    return decorator


def __dir__() -> list[str]:
    return ["require_role", "require_step_up"]


# FastAPI dependency injection helper — services use this rather than building
# their own. Imported lazily so this module does not pull FastAPI at import
# time when used in non-API contexts (e.g. workers).
def auth_dependency(  # noqa: ANN201 — intentionally untyped Depends fn
    validator_factory: Callable[[], Any],
):
    from fastapi import Header

    from fraudnet.auth.token import TokenValidator, extract_principal
    from fraudnet.schemas.errors import AuthError

    async def _resolve(authorization: str | None = Header(default=None)) -> Principal:
        if not authorization or not authorization.startswith("Bearer "):
            raise AuthError("missing bearer token")
        token = authorization.removeprefix("Bearer ").strip()
        validator: TokenValidator = validator_factory()
        claims = await validator.decode(token)
        return extract_principal(claims)

    return _resolve
