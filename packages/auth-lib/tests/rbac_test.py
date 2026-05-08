from __future__ import annotations

from time import time

import pytest

from fraudnet.auth.principal import Principal, Role
from fraudnet.auth.rbac import require_role, require_step_up
from fraudnet.schemas.errors import ForbiddenError, StepUpRequiredError


def _principal(roles: set[Role], step_up_at_ms: int | None = None) -> Principal:
    return Principal(
        subject="u",
        actor_kind="user",
        roles=frozenset(roles),
        tenant_id="mtn-ghana",
        step_up_at_ms=step_up_at_ms,
    )


class TestRequireRole:
    async def test_allows_when_role_present(self) -> None:
        @require_role(Role.FRAUD_LEAD)
        async def handler(*, principal: Principal) -> str:
            return "ok"

        out = await handler(principal=_principal({Role.FRAUD_LEAD}))
        assert out == "ok"

    async def test_rejects_when_role_missing(self) -> None:
        @require_role(Role.FRAUD_LEAD)
        async def handler(*, principal: Principal) -> str:
            return "ok"

        with pytest.raises(ForbiddenError):
            await handler(principal=_principal({Role.NOC_VIEWER}))

    async def test_accepts_any_of_roles(self) -> None:
        @require_role(Role.FRAUD_ANALYST, Role.FRAUD_LEAD)
        async def handler(*, principal: Principal) -> str:
            return "ok"

        assert await handler(principal=_principal({Role.FRAUD_ANALYST})) == "ok"
        assert await handler(principal=_principal({Role.FRAUD_LEAD})) == "ok"

    async def test_no_principal_in_scope_raises(self) -> None:
        @require_role(Role.FRAUD_LEAD)
        async def handler() -> str:
            return "ok"

        with pytest.raises(ForbiddenError):
            await handler()

    def test_rejects_sync_handlers(self) -> None:
        with pytest.raises(TypeError):

            @require_role(Role.FRAUD_LEAD)
            def sync_handler(*, principal: Principal) -> str:  # type: ignore[unused-ignore]
                return "ok"

    def test_zero_roles_rejected(self) -> None:
        with pytest.raises(ValueError):
            require_role()


class TestRequireStepUp:
    async def test_allows_with_fresh_step_up(self) -> None:
        @require_step_up()
        async def handler(*, principal: Principal) -> str:
            return "ok"

        p = _principal({Role.SYSTEM_ADMIN}, step_up_at_ms=int(time() * 1000))
        assert await handler(principal=p) == "ok"

    async def test_rejects_without_step_up(self) -> None:
        @require_step_up()
        async def handler(*, principal: Principal) -> str:
            return "ok"

        with pytest.raises(StepUpRequiredError):
            await handler(principal=_principal({Role.SYSTEM_ADMIN}))
