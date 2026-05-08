"""Read-only API surface for compliance.

Audit lookups by request_id / time range, NDJSON range export, plus
per-regulator templated packs (NCA / DPC / BoG / CSA / GFIC). The
templated exports are async-job-shaped because building a busy month's
pack involves several seconds of database work; the analyst polls
`GET /compliance/export/{job_id}` for the result.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel

from fraudnet.obs import counter, get_logger, metrics_endpoint
from compliance.archive import ArchiveScheduler, IcebergArchiver
from compliance.regulators import REGULATOR_TEMPLATES, REPORT_BUILDERS, render_report_pdf
from compliance.regulators.jobs import ExportJob, JobStore
from compliance.regulators.loader import load_corpus
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


# ---------------------------------------------------------------------------
# Regulator export endpoints
# ---------------------------------------------------------------------------


_EXPORT_JOBS = counter(
    "compliance_export_jobs_total",
    "Regulator export jobs by outcome.",
    labelnames=("regulator", "outcome"),
)


def _job_store(request: Request) -> JobStore:
    return request.app.state.job_store  # type: ignore[no-any-return]


class ExportRequest(BaseModel):
    period_start: datetime
    period_end: datetime
    tenant_id: str = "mtn-ghana"


class ExportJobResponse(BaseModel):
    job_id: str
    regulator: str
    status: str
    period_start: str
    period_end: str
    review_field_count: int = 0
    created_at_ms: int
    updated_at_ms: int
    error: str | None = None


@router.get("/compliance/templates")
async def list_templates() -> dict[str, Any]:
    """Available regulator templates with their submission targets."""
    return {"templates": REGULATOR_TEMPLATES}


@router.post("/compliance/export/{regulator}", response_model=ExportJobResponse)
async def trigger_export(
    regulator: str,
    body: ExportRequest,
    request: Request,
    store: Annotated[AuditStore, Depends(_store)],
    jobs: Annotated[JobStore, Depends(_job_store)],
) -> ExportJobResponse:
    if regulator not in REPORT_BUILDERS:
        raise HTTPException(status_code=400, detail=f"unknown regulator: {regulator}")
    if body.period_start >= body.period_end:
        raise HTTPException(status_code=400, detail="period_start must be before period_end")

    actor_id = request.headers.get("X-Actor-Id")
    job = await jobs.create(
        regulator=regulator,
        period_start=body.period_start.isoformat(),
        period_end=body.period_end.isoformat(),
        tenant_id=body.tenant_id,
        actor_id=actor_id,
    )

    async def _run() -> None:
        try:
            job.status = "running"
            await jobs.update(job)
            corpus = await load_corpus(
                store.pool,
                tenant_id=body.tenant_id,
                period_start=body.period_start.replace(
                    tzinfo=body.period_start.tzinfo or timezone.utc
                ),
                period_end=body.period_end.replace(
                    tzinfo=body.period_end.tzinfo or timezone.utc
                ),
            )
            report = REPORT_BUILDERS[regulator](corpus)
            job.json_payload = _report_to_json(report)
            job.pdf_bytes = render_report_pdf(report)
            job.review_field_count = report.review_field_count
            job.status = "completed"
            _EXPORT_JOBS.labels(regulator=regulator, outcome="ok").inc()
        except Exception as exc:  # noqa: BLE001
            job.status = "failed"
            job.error = str(exc)
            _EXPORT_JOBS.labels(regulator=regulator, outcome="error").inc()
            _log.exception("compliance.export.failed", regulator=regulator)
        await jobs.update(job)

    asyncio.create_task(_run(), name=f"compliance-export-{job.job_id}")

    return ExportJobResponse(
        job_id=job.job_id,
        regulator=job.regulator,
        status=job.status,
        period_start=job.period_start,
        period_end=job.period_end,
        review_field_count=0,
        created_at_ms=job.created_at_ms,
        updated_at_ms=job.updated_at_ms,
    )


@router.get("/compliance/export/{job_id}")
async def export_job_status(
    job_id: str,
    jobs: Annotated[JobStore, Depends(_job_store)],
    format: Annotated[str, Query(pattern="^(status|json|pdf)$")] = "status",
) -> Response:
    """`status` returns the job state; `json` returns the structured payload;
    `pdf` returns the rendered submission packet."""
    job = await jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if format == "status":
        return Response(
            content=ExportJobResponse(
                job_id=job.job_id,
                regulator=job.regulator,
                status=job.status,
                period_start=job.period_start,
                period_end=job.period_end,
                review_field_count=job.review_field_count,
                created_at_ms=job.created_at_ms,
                updated_at_ms=job.updated_at_ms,
                error=job.error,
            ).model_dump_json(),
            media_type="application/json",
        )
    if job.status != "completed":
        raise HTTPException(status_code=409, detail=f"job status is {job.status}")
    if format == "json":
        if job.json_payload is None:
            raise HTTPException(status_code=410, detail="payload no longer available")
        return Response(
            content=json.dumps(job.json_payload, default=str),
            media_type="application/json",
        )
    # format == "pdf"
    if job.pdf_bytes is None:
        raise HTTPException(status_code=410, detail="pdf no longer available")
    return Response(
        content=job.pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{job.regulator}-{job.period_start[:10]}_'
                f'{job.period_end[:10]}.pdf"'
            ),
        },
    )


def _report_to_json(report: Any) -> dict[str, Any]:
    """Convert a RegulatorReport dataclass tree to a JSON-safe dict.

    Inlines `needs_review` so the API response makes the mandatory-fill
    set obvious to the consumer (the reviewer UI / submission portal).
    """
    return {
        "regulator": report.regulator,
        "template_id": report.template_id,
        "period_start": report.period_start,
        "period_end": report.period_end,
        "review_field_count": report.review_field_count,
        "metadata": report.metadata,
        "sections": [
            {
                "title": s.title,
                "fields": [
                    {
                        "name": f.name,
                        "label": f.label,
                        "value": f.value,
                        "needs_review": f.needs_review,
                        "note": f.note,
                    }
                    for f in s.fields
                ],
            }
            for s in report.sections
        ],
    }


# Suppress unused — ExportJob is exported for tests.
_ = ExportJob
