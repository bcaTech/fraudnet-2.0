"""Append-only writers for audit and decision-audit tables."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import asyncpg

from fraudnet.schemas.audit import AuditEventV1
from fraudnet.schemas.events import DecisionDispatchedV1


class AuditStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        import json

        async def init(conn: asyncpg.Connection) -> None:
            await conn.set_type_codec(
                "jsonb",
                encoder=json.dumps,
                decoder=json.loads,
                schema="pg_catalog",
            )

        self._pool = await asyncpg.create_pool(
            self._dsn, min_size=2, max_size=10, init=init
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("AuditStore not connected")
        return self._pool

    async def write_audit_event(self, ev: AuditEventV1) -> None:
        if self._pool is None:
            raise RuntimeError("AuditStore not connected")
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO audit_events
                  (id, actor_id, actor_kind, action, resource_kind, resource_id,
                   purpose, request_id, tenant_id, metadata, event_ts)
                VALUES
                  ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (id) DO NOTHING
                """,
                _audit_uuid(ev.event_id),
                ev.actor_id,
                ev.actor_kind,
                ev.action,
                ev.resource_kind,
                ev.resource_id,
                ev.purpose.value,
                ev.request_id,
                ev.tenant_id,
                dict(ev.metadata),
                _ts(ev.event_ts_ms),
            )

    async def write_decision(self, d: DecisionDispatchedV1) -> None:
        if self._pool is None:
            raise RuntimeError("AuditStore not connected")
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO decision_audits
                  (decision_id, tier, action, subject_kind, subject_id,
                   severity, score, policy_id, policy_version,
                   suppression_key, metadata, tenant_id, event_ts)
                VALUES
                  ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                ON CONFLICT (decision_id) DO NOTHING
                """,
                d.decision_id,
                d.tier.value,
                d.action,
                d.subject.kind.value,
                d.subject.id,
                d.severity.value,
                float(d.score.value) if d.score else None,
                d.policy_id,
                d.policy_version,
                d.suppression_key,
                dict(d.metadata),
                d.tenant_id,
                _ts(d.event_ts_ms),
            )

    async def query_audit_by_request(self, request_id: str) -> list[dict[str, Any]]:
        if self._pool is None:
            raise RuntimeError("AuditStore not connected")
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM audit_events WHERE request_id = $1 ORDER BY event_ts DESC LIMIT 500",
                request_id,
            )
        return [dict(r) for r in rows]

    async def query_audit_range(
        self, *, tenant_id: str, since: datetime, until: datetime, limit: int = 5000
    ) -> list[dict[str, Any]]:
        if self._pool is None:
            raise RuntimeError("AuditStore not connected")
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM audit_events
                 WHERE tenant_id = $1 AND event_ts >= $2 AND event_ts < $3
                 ORDER BY event_ts ASC
                 LIMIT $4
                """,
                tenant_id,
                since,
                until,
                limit,
            )
        return [dict(r) for r in rows]


_AUDIT_NAMESPACE = UUID("9b9d0c1e-9b9d-5c1e-9b9d-0c1e9b9d0c1e")


def _audit_uuid(event_id: str) -> UUID:
    """audit.events.v1 ids are short strings (e.g. aud_<hex24>). Map them to
    UUIDs deterministically so the audit_events.id PK is stable across
    consumer redeliveries."""
    from uuid import uuid5

    return uuid5(_AUDIT_NAMESPACE, event_id)


def _ts(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
