"""Read-only API surface for compliance.

Audit lookups by request_id / time range. The export endpoint streams a
date-range slice as NDJSON; Phase 2 swaps to per-regulator templated packs.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response, StreamingResponse

from fraudnet.obs import get_logger, metrics_endpoint
from compliance.archive import ArchiveScheduler, IcebergArchiver
from compliance.store import AuditStore

_log = get_logger("compliance.api")


def _store(request: Request) -> AuditStore:
    return request.app.state.store  # type: ignore[no-any-return]


def _archiver(request: Request) -> IcebergArchiver | None:
    return getattr(request.app.state, "archiver", None)


def _archive_scheduler(request: Request) -> ArchiveScheduler | None:
    return getattr(request.app.state, "archive_scheduler", None)


router = APIRouter()


@router.get("/health/live", include_in_schema=False)
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", include_in_schema=False)
async def readiness(store: Annotated[AuditStore, Depends(_store)]) -> dict[str, str]:
    return {"status": "ready" if store else "starting"}  # type: ignore[truthy-bool]


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    body, content_type = metrics_endpoint()()
    return PlainTextResponse(body, media_type=content_type)


@router.get("/audit/by_request/{request_id}")
async def by_request(
    request_id: str,
    store: Annotated[AuditStore, Depends(_store)],
) -> list[dict[str, Any]]:
    rows = await store.query_audit_by_request(request_id)
    return [_to_jsonable(r) for r in rows]


@router.get("/audit/range")
async def audit_range(
    store: Annotated[AuditStore, Depends(_store)],
    since: Annotated[datetime, Query()],
    until: Annotated[datetime, Query()],
    tenant_id: Annotated[str, Query()] = "mtn-ghana",
    limit: Annotated[int, Query(ge=1, le=10_000)] = 1000,
) -> list[dict[str, Any]]:
    if since >= until:
        raise HTTPException(status_code=400, detail="since must be before until")
    rows = await store.query_audit_range(
        tenant_id=tenant_id,
        since=since.replace(tzinfo=since.tzinfo or timezone.utc),
        until=until.replace(tzinfo=until.tzinfo or timezone.utc),
        limit=limit,
    )
    return [_to_jsonable(r) for r in rows]


@router.get("/audit/export")
async def export_ndjson(
    store: Annotated[AuditStore, Depends(_store)],
    since: Annotated[datetime, Query()],
    until: Annotated[datetime, Query()],
    tenant_id: Annotated[str, Query()] = "mtn-ghana",
) -> StreamingResponse:
    if since >= until:
        raise HTTPException(status_code=400, detail="since must be before until")

    async def _stream() -> Any:
        rows = await store.query_audit_range(
            tenant_id=tenant_id,
            since=since.replace(tzinfo=since.tzinfo or timezone.utc),
            until=until.replace(tzinfo=until.tzinfo or timezone.utc),
            limit=10_000,
        )
        for r in rows:
            yield (json.dumps(_to_jsonable(r), default=str) + "\n").encode()

    return StreamingResponse(_stream(), media_type="application/x-ndjson")


@router.get("/audit/archived")
async def audit_archived(
    archiver: Annotated[IcebergArchiver | None, Depends(_archiver)],
) -> dict[str, Any]:
    """Months that have been archived to the lakehouse and detached from
    the live audit_events table."""
    if archiver is None:
        return {"enabled": False, "archived": []}
    rows = await archiver.list_archived()
    return {
        "enabled": True,
        "archived": [
            {
                "table_name": r.table_name,
                "year": r.year,
                "month": r.month,
                "rows_archived": r.rows_archived,
                "object_key": r.object_key,
                "sha256": r.sha256,
                "archived_at_ms": r.archived_at_ms,
            }
            for r in rows
        ],
    }


@router.post("/audit/archive/trigger")
async def audit_archive_trigger(
    scheduler: Annotated[ArchiveScheduler | None, Depends(_archive_scheduler)],
) -> dict[str, Any]:
    """Force an archive pass now. Returns the partitions newly archived."""
    if scheduler is None:
        return {"status": "no_scheduler"}
    archived = await scheduler.trigger()
    return {
        "status": "ok",
        "newly_archived": [
            {
                "table_name": r.table_name,
                "year": r.year,
                "month": r.month,
                "rows_archived": r.rows_archived,
                "object_key": r.object_key,
            }
            for r in archived
        ],
    }


def _to_jsonable(row: dict[str, Any]) -> dict[str, Any]:
    """asyncpg returns native types; coerce datetimes / UUIDs / numerics for JSON."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out
