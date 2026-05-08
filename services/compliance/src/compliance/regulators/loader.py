"""Build the period corpus from Postgres.

Single async function that issues 4 parallel queries for the period
window and stitches the result.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import asyncpg

from compliance.regulators.corpus import PeriodCorpus


async def load_corpus(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    period_start: datetime,
    period_end: datetime,
) -> PeriodCorpus:
    audit_q = """
        SELECT id, actor_id, actor_kind, action, resource_kind, resource_id,
               purpose, request_id, tenant_id, metadata, event_ts
          FROM audit_events
         WHERE tenant_id = $1 AND event_ts >= $2 AND event_ts < $3
         ORDER BY event_ts ASC
         LIMIT 5000
    """
    alerts_q = """
        SELECT id, type, severity, subject_kind, subject_id, score, ring_id,
               status, closed_at, closed_reason, details, created_at, updated_at
          FROM alerts
         WHERE tenant_id = $1 AND created_at >= $2 AND created_at < $3
         ORDER BY created_at ASC
         LIMIT 5000
    """
    actions_q = """
        SELECT id, action_kind, tier, status, subject_kind, subject_id,
               taken_at, metadata
          FROM actions_taken
         WHERE tenant_id = $1 AND taken_at >= $2 AND taken_at < $3
         ORDER BY taken_at ASC
         LIMIT 5000
    """
    rings_q = """
        SELECT id, type, status, composite_score, active_since, last_activity,
               member_count, metadata
          FROM rings
         WHERE tenant_id = $1 AND last_activity >= $2 AND last_activity < $3
         ORDER BY last_activity DESC
         LIMIT 1000
    """

    async def _fetch(q: str) -> list[dict[str, Any]]:
        async with pool.acquire() as conn:
            try:
                rows = await conn.fetch(q, tenant_id, period_start, period_end)
            except asyncpg.UndefinedTableError:
                # Some tables (actions_taken, rings) may not be deployed in
                # every environment. Degrade gracefully.
                return []
        return [dict(r) for r in rows]

    audit, alerts, actions, rings = await asyncio.gather(
        _fetch(audit_q),
        _fetch(alerts_q),
        _fetch(actions_q),
        _fetch(rings_q),
    )

    # decisions table is optional in this codebase; fold in if present.
    decisions: list[dict[str, Any]] = []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, decision_kind, tier, signal_id, motif_id, taken_at, metadata
                  FROM decisions
                 WHERE tenant_id = $1 AND taken_at >= $2 AND taken_at < $3
                 ORDER BY taken_at ASC
                 LIMIT 5000
                """,
                tenant_id,
                period_start,
                period_end,
            )
        decisions = [dict(r) for r in rows]
    except asyncpg.UndefinedTableError:
        decisions = []

    return PeriodCorpus(
        period_start=period_start,
        period_end=period_end,
        tenant_id=tenant_id,
        audit_events=tuple(audit),
        alerts=tuple(alerts),
        decisions=tuple(decisions),
        actions_taken=tuple(actions),
        rings=tuple(rings),
    )
