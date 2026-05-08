"""Postgres pool + tenant-scoped repositories for api-enterprise.

Every query carries `tenant_id`. Phase 4 turns on Postgres row-level security
on the enterprise-bearing tables — see `migrations/0001_enterprise_schema.sql`.
The RLS policies key on `current_setting('fraudnet.tenant_id')` which is set
on every connection in `acquire()`.

Set the GUC inside the same connection scope as the query — connection pools
are shared and the GUC must travel with the request, not with the pool.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
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
        import json

        await conn.set_type_codec(
            "jsonb",
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )

    @asynccontextmanager
    async def acquire(self, *, tenant_id: str | None = None) -> AsyncIterator[asyncpg.Connection]:
        if self._pool is None:
            raise RuntimeError("database not connected")
        async with self._pool.acquire() as conn:
            purpose = current_purpose()
            if purpose is not None:
                await conn.execute(
                    "SELECT set_config('fraudnet.purpose', $1, true)",
                    purpose.value,
                )
            if tenant_id is not None:
                await conn.execute(
                    "SELECT set_config('fraudnet.tenant_id', $1, true)",
                    tenant_id,
                )
            yield conn


# ---------------------------------------------------------------------------
# Tenant repository — tenants are the multi-tenant unit of B2B isolation.
# ---------------------------------------------------------------------------


class TenantRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list(self, *, limit: int = 200) -> list[dict[str, Any]]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, slug, name, status, federation_enabled,
                       rate_limit_capacity, rate_limit_refill_per_s,
                       contact_email, created_at, updated_at
                  FROM enterprise_tenants
                 ORDER BY created_at DESC
                 LIMIT $1
                """,
                limit,
            )
        return [dict(r) for r in rows]

    async def get(self, *, tenant_id: str) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, slug, name, status, federation_enabled,
                       rate_limit_capacity, rate_limit_refill_per_s,
                       contact_email, created_at, updated_at
                  FROM enterprise_tenants
                 WHERE slug = $1
                """,
                tenant_id,
            )
        return dict(row) if row else None

    async def create(
        self,
        *,
        slug: str,
        name: str,
        contact_email: str,
        federation_enabled: bool = False,
        rate_limit_capacity: int = 60,
        rate_limit_refill_per_s: float = 10.0,
    ) -> dict[str, Any]:
        from uuid import uuid4

        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO enterprise_tenants (
                    id, slug, name, status, federation_enabled,
                    rate_limit_capacity, rate_limit_refill_per_s, contact_email
                )
                VALUES ($1, $2, $3, 'active', $4, $5, $6, $7)
                RETURNING *
                """,
                uuid4(),
                slug,
                name,
                federation_enabled,
                rate_limit_capacity,
                rate_limit_refill_per_s,
                contact_email,
            )
        assert row is not None
        return dict(row)


# ---------------------------------------------------------------------------
# Tenant-scoped alert / metrics queries
# ---------------------------------------------------------------------------


class EnterpriseAlertRepo:
    """Reads alerts where the subject is a subscriber the tenant tracks.

    The B2B view is narrower than the NOC view: the tenant only sees alerts
    whose subjects are present in the `tenant_subscribers` map. Cross-tenant
    leakage is impossible because the join filters on `tenant_id` and RLS
    on `tenant_subscribers` is keyed to the connection's tenant GUC.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def list(
        self,
        *,
        tenant_id: str,
        status: list[str] | None = None,
        severity: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses = ["a.tenant_id = $1", "ts.tenant_slug = $2"]
        params: list[object] = ["mtn-ghana", tenant_id]
        if status:
            clauses.append(f"a.status = ANY(${len(params) + 1})")
            params.append(status)
        if severity:
            clauses.append(f"a.severity = ANY(${len(params) + 1})")
            params.append(severity)
        sql = (
            "SELECT a.id, a.type, a.severity, a.subject_kind, a.subject_id, "
            "       a.score, a.ring_id, a.status, a.details, a.created_at, a.updated_at "
            "  FROM alerts a "
            "  JOIN tenant_subscribers ts "
            "    ON ts.subscriber_kind = a.subject_kind "
            "   AND ts.subscriber_id   = a.subject_id "
            " WHERE " + " AND ".join(clauses)
            + " ORDER BY a.created_at DESC LIMIT $%d OFFSET $%d"
            % (len(params) + 1, len(params) + 2)
        )
        params.extend([limit, offset])
        async with self._db.acquire(tenant_id=tenant_id) as conn:
            rows = await conn.fetch(sql, *params)
        return [dict(r) for r in rows]

    async def dashboard(self, *, tenant_id: str) -> dict[str, Any]:
        """Tenant-scoped fraud KPIs: open alerts, severity mix, recent rate."""
        async with self._db.acquire(tenant_id=tenant_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    count(*) FILTER (
                        WHERE a.status NOT IN ('closed', 'fp')
                    )                                              AS open_alerts,
                    count(*) FILTER (
                        WHERE a.created_at > now() - interval '24 hours'
                    )                                              AS recent_24h,
                    count(*) FILTER (
                        WHERE a.created_at > now() - interval '7 days'
                    )                                              AS recent_7d,
                    count(*) FILTER (WHERE a.severity = 'critical') AS critical,
                    count(*) FILTER (WHERE a.severity = 'high')     AS high,
                    count(*) FILTER (WHERE a.severity = 'medium')   AS medium,
                    count(*) FILTER (WHERE a.severity = 'low')      AS low
                  FROM alerts a
                  JOIN tenant_subscribers ts
                    ON ts.subscriber_kind = a.subject_kind
                   AND ts.subscriber_id   = a.subject_id
                 WHERE ts.tenant_slug = $1
                """,
                tenant_id,
            )
            blocked_24h = await conn.fetchval(
                """
                SELECT count(*)
                  FROM actions_taken at
                  JOIN tenant_subscribers ts
                    ON ts.subscriber_kind = at.subject_kind
                   AND ts.subscriber_id   = at.subject_id
                 WHERE ts.tenant_slug = $1
                   AND at.action_kind IN ('volte_tag', 'url_block', 'momo_friction')
                   AND at.taken_at > now() - interval '24 hours'
                """,
                tenant_id,
            )
        return {
            "open_alerts": int(row["open_alerts"]) if row else 0,
            "recent_24h": int(row["recent_24h"]) if row else 0,
            "recent_7d": int(row["recent_7d"]) if row else 0,
            "by_severity": {
                "critical": int(row["critical"]) if row else 0,
                "high": int(row["high"]) if row else 0,
                "medium": int(row["medium"]) if row else 0,
                "low": int(row["low"]) if row else 0,
            },
            "blocked_24h": int(blocked_24h or 0),
        }


# ---------------------------------------------------------------------------
# Shared flags: cross-tenant intel exchange.
# ---------------------------------------------------------------------------


class SharedFlagRepo:
    """Flags shared between tenants via the federation protocol.

    Hashed identifiers only — see `packages/federation` for the hashing
    rules and `migrations/0001_enterprise_schema.sql` for the schema.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def list_for_tenant(
        self,
        *,
        tenant_id: str,
        direction: str = "all",  # 'incoming' | 'outgoing' | 'all'
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[object] = [tenant_id]
        if direction == "incoming":
            clauses.append("recipient_tenant = $1")
        elif direction == "outgoing":
            clauses.append("sender_tenant = $1")
        else:
            clauses.append("(sender_tenant = $1 OR recipient_tenant = $1)")
        sql = (
            "SELECT id, sender_tenant, recipient_tenant, identifier_kind, "
            "       identifier_hash, indicator_kind, confidence, evidence, "
            "       shared_at, expires_at "
            "  FROM shared_flags "
            " WHERE " + " AND ".join(clauses)
            + " ORDER BY shared_at DESC LIMIT $2"
        )
        params.append(limit)
        async with self._db.acquire(tenant_id=tenant_id) as conn:
            rows = await conn.fetch(sql, *params)
        return [dict(r) for r in rows]

    async def submit(
        self,
        *,
        sender_tenant: str,
        recipient_tenant: str,
        identifier_kind: str,
        identifier_hash: str,
        indicator_kind: str,
        confidence: float,
        evidence: dict[str, Any],
        ttl_days: int = 30,
    ) -> dict[str, Any]:
        from uuid import uuid4

        async with self._db.acquire(tenant_id=sender_tenant) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO shared_flags (
                    id, sender_tenant, recipient_tenant, identifier_kind,
                    identifier_hash, indicator_kind, confidence, evidence,
                    shared_at, expires_at
                )
                VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8,
                    now(), now() + ($9::int || ' days')::interval
                )
                RETURNING *
                """,
                uuid4(),
                sender_tenant,
                recipient_tenant,
                identifier_kind,
                identifier_hash,
                indicator_kind,
                confidence,
                evidence,
                ttl_days,
            )
        assert row is not None
        return dict(row)


# ---------------------------------------------------------------------------
# Block requests: cross-network blocks initiated by a tenant.
# ---------------------------------------------------------------------------


class BlockRequestRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def submit(
        self,
        *,
        tenant_id: str,
        target_kind: str,
        target_value: str,
        reason: str,
        requested_by: UUID,
    ) -> dict[str, Any]:
        from uuid import uuid4

        async with self._db.acquire(tenant_id=tenant_id) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO enterprise_block_requests (
                    id, tenant_slug, target_kind, target_value,
                    reason, status, requested_by
                )
                VALUES ($1, $2, $3, $4, $5, 'pending_review', $6)
                RETURNING *
                """,
                uuid4(),
                tenant_id,
                target_kind,
                target_value,
                reason,
                requested_by,
            )
        assert row is not None
        return dict(row)

    async def list(
        self, *, tenant_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        async with self._db.acquire(tenant_id=tenant_id) as conn:
            rows = await conn.fetch(
                """
                SELECT id, tenant_slug, target_kind, target_value, reason,
                       status, requested_by, requested_at, decided_at,
                       decision_notes
                  FROM enterprise_block_requests
                 WHERE tenant_slug = $1
                 ORDER BY requested_at DESC
                 LIMIT $2
                """,
                tenant_id,
                limit,
            )
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Group-level (cross-tenant) — only for GROUP_ADMIN.
# ---------------------------------------------------------------------------


class GroupAnalyticsRepo:
    """Cross-tenant aggregates. Bypasses tenant filters by design — guarded
    upstream by `@require_role(Role.GROUP_ADMIN)`."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def overview(self) -> dict[str, Any]:
        async with self._db.acquire() as conn:
            tenants = await conn.fetchval(
                "SELECT count(*) FROM enterprise_tenants WHERE status = 'active'"
            )
            row = await conn.fetchrow(
                """
                SELECT
                    count(*) FILTER (
                        WHERE status NOT IN ('closed', 'fp')
                    )                                                  AS open_alerts,
                    count(*) FILTER (
                        WHERE created_at > now() - interval '24 hours'
                    )                                                  AS recent_24h,
                    count(*) FILTER (WHERE severity = 'critical')      AS critical,
                    count(*) FILTER (WHERE severity = 'high')          AS high,
                    count(DISTINCT subject_id)                          AS distinct_subjects
                  FROM alerts
                """
            )
            cross_opco = await conn.fetchval(
                """
                SELECT count(*)
                  FROM rings
                 WHERE type = 'cross_opco'
                   AND status IN ('monitoring', 'takedown')
                """
            )
        return {
            "active_tenants": int(tenants or 0),
            "open_alerts": int(row["open_alerts"]) if row else 0,
            "recent_24h": int(row["recent_24h"]) if row else 0,
            "by_severity": {
                "critical": int(row["critical"]) if row else 0,
                "high": int(row["high"]) if row else 0,
            },
            "distinct_subjects": int(row["distinct_subjects"]) if row else 0,
            "cross_opco_rings": int(cross_opco or 0),
        }

    async def cross_opco_rings(self, *, limit: int = 50) -> list[dict[str, Any]]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, type, status, composite_score, active_since,
                       last_activity, member_count, metadata
                  FROM rings
                 WHERE type = 'cross_opco'
                 ORDER BY last_activity DESC
                 LIMIT $1
                """,
                limit,
            )
        return [dict(r) for r in rows]

    async def trending_motifs(self, *, window_hours: int = 24) -> list[dict[str, Any]]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT details ->> 'motif' AS motif,
                       count(*)             AS hits,
                       count(DISTINCT tenant_id) AS distinct_tenants,
                       max(created_at)      AS last_seen
                  FROM alerts
                 WHERE details ? 'motif'
                   AND created_at > now() - ($1::int || ' hours')::interval
                 GROUP BY details ->> 'motif'
                 ORDER BY hits DESC
                 LIMIT 50
                """,
                window_hours,
            )
        return [dict(r) for r in rows]
