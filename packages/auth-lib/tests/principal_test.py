from __future__ import annotations

from time import time

from fraudnet.auth.principal import Principal, Role


def _p(roles: set[Role], step_up_at_ms: int | None = None) -> Principal:
    return Principal(
        subject="u-1",
        actor_kind="user",
        roles=frozenset(roles),
        tenant_id="mtn-ghana",
        step_up_at_ms=step_up_at_ms,
    )


class TestPrincipalRoles:
    def test_has_role(self) -> None:
        p = _p({Role.FRAUD_ANALYST})
        assert p.has_role(Role.FRAUD_ANALYST)
        assert not p.has_role(Role.FRAUD_LEAD)

    def test_has_any(self) -> None:
        p = _p({Role.FRAUD_ANALYST})
        assert p.has_any(Role.FRAUD_ANALYST, Role.FRAUD_LEAD)
        assert not p.has_any(Role.SYSTEM_ADMIN, Role.AUDITOR)


class TestStepUp:
    def test_no_step_up(self) -> None:
        assert not _p({Role.FRAUD_LEAD}).has_step_up()

    def test_fresh_step_up(self) -> None:
        now = int(time() * 1000)
        assert _p({Role.FRAUD_LEAD}, step_up_at_ms=now).has_step_up()

    def test_stale_step_up(self) -> None:
        # Older than 5 min default.
        now = int(time() * 1000)
        assert not _p({Role.FRAUD_LEAD}, step_up_at_ms=now - 600_000).has_step_up()

    def test_custom_max_age(self) -> None:
        now = int(time() * 1000)
        p = _p({Role.FRAUD_LEAD}, step_up_at_ms=now - 600_000)
        assert p.has_step_up(max_age_ms=900_000)
