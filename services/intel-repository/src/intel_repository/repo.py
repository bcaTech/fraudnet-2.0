"""Postgres repository + Redis hot-lookup cache.

`upsert_entry` is the single ingestion path used by the auto-populator
and by the manual /intel/contribute endpoint. It either inserts a new
entry or boosts an existing one (hit_count, risk_score, last_seen_at,
expires_at).

Risk score on conflict is `max(existing, new)` — repository risk only
goes up over the active life of the entry. Decay happens via the TTL
expiration: stale entries deactivate, fresh evidence creates a new
entry with reset risk.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Iterable
from uuid import UUID, uuid4

import asyncpg


VALID_KINDS = frozenset(
    {
        "suspect_number",
        "high_risk_destination",
        "unallocated_range",
        "scam_template",
        "spoof_indicator",
        "agent_risk",
    }
)

# Kinds that benefit from sub-ms Redis lookups during the scoring path.
# Other kinds are queried at investigator latency.
HOT_KINDS = frozenset({"suspect_number", "spoof_indicator", "scam_template"})


class Database:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
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
            raise RuntimeError("Database not connected")
        return self._pool

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        if self._pool is None:
            raise RuntimeError("Database not connected")
        async with self._pool.acquire() as conn:
            yield conn


class IntelRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert_entry(
        self,
        *,
        kind: str,
        identifier: str,
        risk_score: float,
        ttl_s: int,
        contributor: str,
        metadata: dict[str, Any] | None = None,
        tenant_id: str = "mtn-ghana",
    ) -> dict[str, Any]:
        if kind not in VALID_KINDS:
            raise ValueError(f"unknown kind: {kind}")
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO intel_entries (
                    id, tenant_id, kind, identifier, metadata, risk_score,
                    hit_count, first_seen_at, last_seen_at, expires_at,
                    contributor, active
                )
                VALUES (
                    $1, $2, $3, $4, $5, $6,
                    1, now(), now(), now() + ($7::int || ' seconds')::interval,
                    $8, TRUE
                )
                ON CONFLICT (tenant_id, kind, identifier) DO UPDATE SET
                    metadata     = intel_entries.metadata || EXCLUDED.metadata,
                    risk_score   = GREATEST(intel_entries.risk_score, EXCLUDED.risk_score),
                    hit_count    = intel_entries.hit_count + 1,
                    last_seen_at = now(),
                    expires_at   = now() + ($7::int || ' seconds')::interval,
                    active       = TRUE
                RETURNING *
                """,
                uuid4(),
                tenant_id,
                kind,
                identifier,
                metadata or {},
                risk_score,
                ttl_s,
                contributor,
            )
        assert row is not None
        return dict(row)

    async def get(
        self, *, kind: str, identifier: str, tenant_id: str = "mtn-ghana"
    ) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM intel_entries
                 WHERE tenant_id = $1 AND kind = $2 AND identifier = $3
                   AND active AND expires_at > now()
                """,
                tenant_id,
                kind,
                identifier,
            )
        return dict(row) if row else None

    async def list_by_kind(
        self,
        *,
        kind: str,
        tenant_id: str = "mtn-ghana",
        page: int = 1,
        limit: int = 100,
        min_score: float = 0.0,
    ) -> tuple[list[dict[str, Any]], int]:
        if kind not in VALID_KINDS:
            raise ValueError(f"unknown kind: {kind}")
        offset = max(0, (page - 1) * limit)
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, kind, identifier, metadata, risk_score, hit_count,
                       first_seen_at, last_seen_at, expires_at, contributor
                  FROM intel_entries
                 WHERE tenant_id = $1 AND kind = $2 AND active
                   AND expires_at > now() AND risk_score >= $3
                 ORDER BY risk_score DESC, last_seen_at DESC
                 LIMIT $4 OFFSET $5
                """,
                tenant_id,
                kind,
                min_score,
                limit,
                offset,
            )
            total = await conn.fetchval(
                """
                SELECT count(*) FROM intel_entries
                 WHERE tenant_id = $1 AND kind = $2 AND active
                   AND expires_at > now() AND risk_score >= $3
                """,
                tenant_id,
                kind,
                min_score,
            )
        return [dict(r) for r in rows], int(total or 0)

    async def stats(self, *, tenant_id: str = "mtn-ghana") -> dict[str, Any]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT kind, count(*) FILTER (WHERE expires_at > now()) AS active_count,
                       count(*) AS total_count,
                       round(avg(risk_score) FILTER (WHERE expires_at > now()), 3)
                         AS avg_risk_score,
                       max(last_seen_at) AS most_recent_at
                  FROM intel_entries
                 WHERE tenant_id = $1 AND active
                 GROUP BY kind
                """,
                tenant_id,
            )
            recent = await conn.fetchval(
                """
                SELECT count(*) FROM intel_entries
                 WHERE tenant_id = $1 AND active
                   AND last_seen_at > now() - interval '24 hours'
                """,
                tenant_id,
            )
        return {
            "by_kind": [dict(r) for r in rows],
            "added_or_refreshed_24h": int(recent or 0),
        }

    async def expire_stale(self, *, tenant_id: str = "mtn-ghana") -> int:
        """Mark entries past expires_at as inactive. Run on a cron tick."""
        async with self._db.acquire() as conn:
            updated = await conn.execute(
                """
                UPDATE intel_entries
                   SET active = FALSE
                 WHERE tenant_id = $1 AND active AND expires_at <= now()
                """,
                tenant_id,
            )
        # asyncpg returns "UPDATE n"
        try:
            return int(updated.rsplit(" ", 1)[-1])
        except ValueError:
            return 0
