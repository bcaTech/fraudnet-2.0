"""Postgres connection pool + repository helpers.

Repositories are thin async wrappers over asyncpg. They:
  - Set the `fraudnet.purpose` GUC on each connection so RLS can enforce
    purpose-limitation in production (no-op in Phase 1 since the migrations
    don't yet define RLS policies; the GUC is harmless either way).
  - Carry tenant_id through every read/write (Phase 4 multi-tenant).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Iterable
from uuid import UUID

import asyncpg

from fraudnet.audit.purpose import current_purpose


class Database:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(
            self._dsn,
            min_size=2,
            max_size=10,
            init=self._init_connection,
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()

    @staticmethod
    async def _init_connection(conn: asyncpg.Connection) -> None:
        # JSONB encode/decode helpers — keep payloads as Python dicts.
        import json

        await conn.set_type_codec(
            "jsonb",
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        if self._pool is None:
            raise RuntimeError("database not connected")
        async with self._pool.acquire() as conn:
            purpose = current_purpose()
            if purpose is not None:
                await conn.execute(
                    "SELECT set_config('fraudnet.purpose', $1, true)",
                    purpose.value,
                )
            yield conn


# ---------------------------------------------------------------------------
# Alert repository
# ---------------------------------------------------------------------------


class AlertRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list(
        self,
        *,
        tenant_id: str,
        status: Iterable[str] | None = None,
        severity: Iterable[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses = ["tenant_id = $1"]
        params: list[object] = [tenant_id]
        if status:
            clauses.append(f"status = ANY(${len(params) + 1})")
            params.append(list(status))
        if severity:
            clauses.append(f"severity = ANY(${len(params) + 1})")
            params.append(list(severity))
        sql = (
            "SELECT id, type, severity, subject_kind, subject_id, score, "
            "ring_id, status, assignee_id, closed_at, closed_reason, details, "
            "decision_id, created_at, updated_at "
            "FROM alerts WHERE " + " AND ".join(clauses)
            + " ORDER BY created_at DESC LIMIT $%d OFFSET $%d"
            % (len(params) + 1, len(params) + 2)
        )
        params.extend([limit, offset])
        async with self._db.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [dict(r) for r in rows]

    async def get(self, *, tenant_id: str, alert_id: UUID) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM alerts WHERE id = $1 AND tenant_id = $2",
                alert_id,
                tenant_id,
            )
        return dict(row) if row else None

    async def claim(
        self, *, tenant_id: str, alert_id: UUID, assignee_id: UUID
    ) -> dict[str, Any] | None:
        """Claim an alert. Returns the updated row, or None if the alert was
        already claimed by another user (race-safe via the WHERE clause)."""
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE alerts
                   SET status = 'claimed',
                       assignee_id = $3,
                       updated_at = now()
                 WHERE id = $1 AND tenant_id = $2 AND status = 'new'
                 RETURNING *
                """,
                alert_id,
                tenant_id,
                assignee_id,
            )
        return dict(row) if row else None

    async def close(
        self,
        *,
        tenant_id: str,
        alert_id: UUID,
        actor_id: UUID,
        reason: str,
        is_false_positive: bool,
    ) -> dict[str, Any] | None:
        new_status = "fp" if is_false_positive else "closed"
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE alerts
                   SET status = $4,
                       assignee_id = $3,
                       closed_at = now(),
                       closed_reason = $5,
                       updated_at = now()
                 WHERE id = $1 AND tenant_id = $2
                 RETURNING *
                """,
                alert_id,
                tenant_id,
                actor_id,
                new_status,
                reason,
            )
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Ring repository
# ---------------------------------------------------------------------------


class RingRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list(
        self,
        *,
        tenant_id: str,
        status: Iterable[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses = ["tenant_id = $1"]
        params: list[object] = [tenant_id]
        if status:
            clauses.append(f"status = ANY(${len(params) + 1})")
            params.append(list(status))
        sql = (
            "SELECT id, type, status, composite_score, active_since, "
            "last_activity, member_count, metadata, created_at, updated_at "
            "FROM rings WHERE " + " AND ".join(clauses)
            + " ORDER BY last_activity DESC LIMIT $%d OFFSET $%d"
            % (len(params) + 1, len(params) + 2)
        )
        params.extend([limit, offset])
        async with self._db.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [dict(r) for r in rows]

    async def get(
        self, *, tenant_id: str, ring_id: UUID
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        async with self._db.acquire() as conn:
            ring = await conn.fetchrow(
                "SELECT * FROM rings WHERE id = $1 AND tenant_id = $2",
                ring_id,
                tenant_id,
            )
            if not ring:
                return None, []
            members = await conn.fetch(
                "SELECT * FROM ring_members WHERE ring_id = $1 ORDER BY confidence DESC NULLS LAST",
                ring_id,
            )
        return dict(ring), [dict(m) for m in members]


# ---------------------------------------------------------------------------
# Takedown repository
# ---------------------------------------------------------------------------


_TAKEDOWN_TRANSITIONS: dict[str, frozenset[str]] = {
    "drafted": frozenset({"approved", "closed"}),
    "approved": frozenset({"filed", "closed"}),
    "filed": frozenset({"acknowledged", "closed"}),
    "acknowledged": frozenset({"executed", "closed"}),
    "executed": frozenset({"closed"}),
    "closed": frozenset(),
}


def is_valid_transition(from_status: str, to_status: str) -> bool:
    return to_status in _TAKEDOWN_TRANSITIONS.get(from_status, frozenset())


class TakedownRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(
        self,
        *,
        tenant_id: str,
        ring_id: UUID,
        created_by: UUID,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        from uuid import uuid4

        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO takedowns (id, ring_id, status, metadata, tenant_id, created_by)
                VALUES ($1, $2, 'drafted', $3, $4, $5)
                RETURNING *
                """,
                uuid4(),
                ring_id,
                metadata,
                tenant_id,
                created_by,
            )
        assert row is not None
        return dict(row)

    async def transition(
        self,
        *,
        tenant_id: str,
        takedown_id: UUID,
        target: str,
        filed_with: str | None = None,
    ) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            current = await conn.fetchval(
                "SELECT status FROM takedowns WHERE id = $1 AND tenant_id = $2",
                takedown_id,
                tenant_id,
            )
            if current is None:
                return None
            if not is_valid_transition(current, target):
                raise ValueError(f"invalid transition {current} → {target}")
            row = await conn.fetchrow(
                """
                UPDATE takedowns
                   SET status = $3,
                       filed_with = COALESCE($4, filed_with),
                       filed_at = CASE WHEN $3 = 'filed' THEN now() ELSE filed_at END,
                       updated_at = now()
                 WHERE id = $1 AND tenant_id = $2
                 RETURNING *
                """,
                takedown_id,
                tenant_id,
                target,
                filed_with,
            )
        return dict(row) if row else None
