"""Postgres pool + watchlist repositories."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from uuid import UUID, uuid4

import asyncpg


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
            yield conn


# ---------------------------------------------------------------------------
# Watchlist entries
# ---------------------------------------------------------------------------


class WatchlistRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def replace_source(
        self,
        *,
        source: str,
        refresh_id: str,
        rows: list[dict[str, Any]],
    ) -> int:
        """Atomic replace: deactivate prior rows, insert new ones.

        Why deactivate rather than delete: keeps audit history. The
        non-active rows are not surfaced by `match_*` queries — they're
        kept for forensic review of what *was* on the list at a given time.
        """
        async with self._db.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE watchlist_entries SET active = FALSE, updated_at = now() "
                    "WHERE source = $1 AND active = TRUE",
                    source,
                )
                inserted = 0
                for r in rows:
                    await conn.execute(
                        """
                        INSERT INTO watchlist_entries (
                            id, source, refresh_id, external_id, category, name,
                            aliases, date_of_birth, country, msisdns,
                            national_ids, metadata, active
                        )
                        VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, TRUE
                        )
                        """,
                        uuid4(),
                        source,
                        refresh_id,
                        r.get("external_id"),
                        r["category"],
                        r["name"],
                        r.get("aliases", []),
                        r.get("date_of_birth"),
                        r.get("country"),
                        r.get("msisdns", []),
                        r.get("national_ids", []),
                        r.get("metadata", {}),
                    )
                    inserted += 1
                await conn.execute(
                    """
                    INSERT INTO watchlist_sources (
                        source, last_refresh_at, last_refresh_id,
                        last_refresh_status, entry_count, updated_at
                    )
                    VALUES ($1, now(), $2, 'success', $3, now())
                    ON CONFLICT (source) DO UPDATE SET
                        last_refresh_at = now(),
                        last_refresh_id = EXCLUDED.last_refresh_id,
                        last_refresh_status = 'success',
                        last_error = NULL,
                        entry_count = EXCLUDED.entry_count,
                        updated_at = now()
                    """,
                    source,
                    refresh_id,
                    inserted,
                )
        return inserted

    async def add_internal(
        self,
        *,
        category: str,
        name: str,
        aliases: list[str] | None = None,
        msisdns: list[str] | None = None,
        national_ids: list[str] | None = None,
        country: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO watchlist_entries (
                    id, source, refresh_id, category, name, aliases,
                    country, msisdns, national_ids, metadata, active
                )
                VALUES ($1, 'internal', $2, $3, $4, $5, $6, $7, $8, $9, TRUE)
                RETURNING *
                """,
                uuid4(),
                f"manual-{uuid4().hex[:8]}",
                category,
                name,
                aliases or [],
                country,
                msisdns or [],
                national_ids or [],
                metadata or {},
            )
        assert row is not None
        return dict(row)

    async def list_active_names(
        self, *, source: str | None = None, limit: int = 100_000
    ) -> list[dict[str, Any]]:
        clauses = ["active = TRUE"]
        params: list[object] = []
        if source:
            clauses.append(f"source = ${len(params) + 1}")
            params.append(source)
        sql = (
            "SELECT id, source, category, name, aliases, country, msisdns, national_ids "
            "  FROM watchlist_entries WHERE " + " AND ".join(clauses)
            + f" LIMIT ${len(params) + 1}"
        )
        params.append(limit)
        async with self._db.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [dict(r) for r in rows]

    async def find_by_msisdn(self, msisdn: str) -> list[dict[str, Any]]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM watchlist_entries WHERE active = TRUE AND $1 = ANY(msisdns)",
                msisdn,
            )
        return [dict(r) for r in rows]

    async def find_by_national_id(self, national_id: str) -> list[dict[str, Any]]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM watchlist_entries WHERE active = TRUE AND $1 = ANY(national_ids)",
                national_id,
            )
        return [dict(r) for r in rows]

    async def stats(self) -> dict[str, Any]:
        async with self._db.acquire() as conn:
            counts = await conn.fetch(
                """
                SELECT source, count(*) AS active_count
                  FROM watchlist_entries
                 WHERE active = TRUE
                 GROUP BY source
                """
            )
            sources = await conn.fetch(
                "SELECT source, last_refresh_at, last_refresh_status, "
                "entry_count FROM watchlist_sources ORDER BY source"
            )
            recent = await conn.fetchrow(
                """
                SELECT
                    count(*) FILTER (WHERE outcome = 'hit') AS hits,
                    count(*) FILTER (WHERE outcome = 'miss') AS misses,
                    count(*) AS total
                  FROM watchlist_match_log
                 WHERE created_at > now() - interval '24 hours'
                """
            )
        return {
            "active_by_source": {r["source"]: r["active_count"] for r in counts},
            "sources": [dict(r) for r in sources],
            "checks_24h": dict(recent) if recent else {},
        }


# ---------------------------------------------------------------------------
# Match audit
# ---------------------------------------------------------------------------


class MatchLogRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def log(
        self,
        *,
        query_kind: str,
        query_value_hash: str,
        matched_entry_id: UUID | None,
        match_score: float | None,
        threshold: float,
        outcome: str,
        caller: str | None,
    ) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO watchlist_match_log (
                    id, query_kind, query_value_hash, matched_entry_id,
                    match_score, threshold, outcome, caller
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                uuid4(),
                query_kind,
                query_value_hash,
                matched_entry_id,
                match_score,
                threshold,
                outcome,
                caller,
            )
