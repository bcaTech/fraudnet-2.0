"""aml-watchlist routes."""

from __future__ import annotations

import csv
import io
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, Request, UploadFile
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

from fraudnet.audit import record, with_purpose
from fraudnet.auth.principal import Principal, Role
from fraudnet.auth.rbac import require_role, require_step_up
from fraudnet.obs import get_logger, metrics_endpoint
from fraudnet.schemas.types import Purpose
from aml_watchlist.db import WatchlistRepo
from aml_watchlist.feeds import parse_ofac_csv, parse_un_xml
from aml_watchlist.matcher import MatchEngine

_log = get_logger("aml_watchlist.api")


router = APIRouter()


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def _repo(request: Request) -> WatchlistRepo:
    return request.app.state.repo  # type: ignore[no-any-return]


def _engine(request: Request) -> MatchEngine:
    return request.app.state.engine  # type: ignore[no-any-return]


def _principal(request: Request) -> Principal:
    p = getattr(request.state, "principal", None)
    if p is None:
        raise HTTPException(status_code=401, detail="auth required")
    return p  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CheckResponse(BaseModel):
    hit: bool
    score: float
    threshold: float
    matched_entry_id: str | None = None
    matched_name: str | None = None
    source: str | None = None
    category: str | None = None
    explanation: dict[str, Any] | None = None


class InternalAddBody(BaseModel):
    category: str = Field(min_length=1, max_length=32)  # 'sanctions' | 'pep' | 'criminal' | 'internal'
    name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    msisdns: list[str] = Field(default_factory=list, max_length=20)
    national_ids: list[str] = Field(default_factory=list, max_length=20)
    country: str | None = Field(default=None, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ImportBody(BaseModel):
    source: str = Field(pattern="^(un|ofac|gfic|internal)$")
    format: str = Field(pattern="^(csv|xml|json)$")
    body: str = Field(min_length=1, max_length=20_000_000)


# ---------------------------------------------------------------------------
# Health / metrics
# ---------------------------------------------------------------------------


@router.get("/health/live", include_in_schema=False)
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", include_in_schema=False)
async def readiness() -> dict[str, str]:
    return {"status": "ready"}


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    body, content_type = metrics_endpoint()()
    return PlainTextResponse(body, media_type=content_type)


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


@router.get("/watchlist/check/{kind}/{value}", response_model=CheckResponse)
async def check(
    kind: Annotated[str, Path(pattern="^(name|msisdn|national_id)$")],
    value: str,
    engine: Annotated[MatchEngine, Depends(_engine)],
    request: Request,
) -> CheckResponse:
    """Real-time lookup. No purpose claim — these queries are by design
    integrated into the scoring path; the audit log captures every check.

    The value is hashed for the audit log; we never persist the raw query.
    """
    if not value or len(value) > 256:
        raise HTTPException(status_code=400, detail="invalid value")
    caller = request.headers.get("X-Caller-Service") or "unknown"
    if kind == "msisdn":
        result = await engine.check_msisdn(value, caller=caller)
    elif kind == "national_id":
        result = await engine.check_national_id(value, caller=caller)
    else:
        result = await engine.check_name(value, caller=caller)

    explanation: dict[str, Any] | None = None
    if result.explanation is not None:
        explanation = {
            "jaro_winkler": result.explanation.jaro_winkler_score,
            "soundex_match": result.explanation.soundex_match,
            "metaphone_match": result.explanation.metaphone_match,
            "matched_tokens": [
                {"query": q, "candidate": c}
                for q, c in result.explanation.matched_tokens
            ],
        }
    return CheckResponse(
        hit=result.hit,
        score=result.score,
        threshold=result.threshold,
        matched_entry_id=str(result.entry["id"]) if result.entry else None,
        matched_name=result.entry["name"] if result.entry else None,
        source=result.entry["source"] if result.entry else None,
        category=result.entry["category"] if result.entry else None,
        explanation=explanation,
    )


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@router.get("/watchlist/stats")
@require_role(
    Role.NOC_VIEWER,
    Role.FRAUD_ANALYST,
    Role.FRAUD_LEAD,
    Role.FRAUD_MANAGER,
    Role.SYSTEM_ADMIN,
)
async def stats(
    repo: Annotated[WatchlistRepo, Depends(_repo)],
    principal: Annotated[Principal, Depends(_principal)],
) -> dict[str, Any]:
    with with_purpose(Purpose.AUDIT):
        out = await repo.stats()
        await record(
            action="aml.stats.read",
            resource_kind="watchlist",
            resource_id="stats",
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
        )
    return out


# ---------------------------------------------------------------------------
# Import (manual)
# ---------------------------------------------------------------------------


@router.post("/watchlist/import")
@require_step_up()
@require_role(Role.SYSTEM_ADMIN)
async def import_bulk(
    body: ImportBody,
    repo: Annotated[WatchlistRepo, Depends(_repo)],
    principal: Annotated[Principal, Depends(_principal)],
) -> dict[str, Any]:
    """Bulk import — UN XML, OFAC CSV, GFIC CSV, or internal JSON list.

    UN/OFAC parsers are reused so an out-of-band manual refresh follows
    the same code path as the scheduled cron.
    """
    if body.source == "un" and body.format == "xml":
        rows = parse_un_xml(body.body)
    elif body.source == "ofac" and body.format == "csv":
        rows = parse_ofac_csv(body.body)
    elif body.format == "csv":
        rows = _parse_generic_csv(body.body)
    elif body.format == "json":
        import json

        try:
            payload = json.loads(body.body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"invalid json: {exc}") from exc
        if not isinstance(payload, list):
            raise HTTPException(status_code=400, detail="json body must be a list")
        rows = [_normalise_json_row(r, source=body.source) for r in payload]
    else:
        raise HTTPException(
            status_code=400, detail=f"unsupported format: {body.format}"
        )

    refresh_id = f"manual-{body.source}-{uuid4().hex[:8]}"
    count = await repo.replace_source(
        source=body.source, refresh_id=refresh_id, rows=rows
    )
    with with_purpose(Purpose.AUDIT):
        await record(
            action="aml.import",
            resource_kind="watchlist",
            resource_id=body.source,
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
            metadata={
                "rows": str(count),
                "format": body.format,
                "refresh_id": refresh_id,
            },
        )
    return {"source": body.source, "imported": count, "refresh_id": refresh_id}


@router.post("/watchlist/internal/add")
@require_role(Role.FRAUD_LEAD, Role.FRAUD_MANAGER, Role.SYSTEM_ADMIN)
async def add_internal(
    body: InternalAddBody,
    repo: Annotated[WatchlistRepo, Depends(_repo)],
    principal: Annotated[Principal, Depends(_principal)],
) -> dict[str, Any]:
    """Add to the operator-defined internal watchlist.

    Lower trust threshold than UN/OFAC — internal entries are typically
    suspect numbers or known mules from prior investigations.
    """
    row = await repo.add_internal(
        category=body.category,
        name=body.name,
        aliases=body.aliases,
        msisdns=body.msisdns,
        national_ids=body.national_ids,
        country=body.country,
        metadata=body.metadata,
    )
    with with_purpose(Purpose.AUDIT):
        await record(
            action="aml.internal.add",
            resource_kind="watchlist_entry",
            resource_id=str(row["id"]),
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
            metadata={"category": body.category},
        )
    return {"id": str(row["id"]), "name": row["name"]}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_generic_csv(csv_text: str) -> list[dict[str, Any]]:
    """Generic CSV with header row (name, aliases, msisdns, national_ids, ...).

    Used for GFIC + internal one-off uploads. Aliases/msisdns/national_ids
    are pipe-delimited within a single CSV cell.
    """
    out: list[dict[str, Any]] = []
    reader = csv.DictReader(io.StringIO(csv_text))
    for r in reader:
        name = (r.get("name") or "").strip()
        if not name:
            continue
        out.append(
            {
                "external_id": (r.get("external_id") or "").strip() or None,
                "category": (r.get("category") or "internal").strip(),
                "name": name,
                "aliases": [a.strip() for a in (r.get("aliases") or "").split("|") if a.strip()],
                "msisdns": [a.strip() for a in (r.get("msisdns") or "").split("|") if a.strip()],
                "national_ids": [
                    a.strip() for a in (r.get("national_ids") or "").split("|") if a.strip()
                ],
                "country": (r.get("country") or "").strip() or None,
                "metadata": {},
            }
        )
    return out


def _normalise_json_row(row: dict[str, Any], *, source: str) -> dict[str, Any]:
    return {
        "external_id": row.get("external_id"),
        "category": row.get("category", source),
        "name": row["name"],
        "aliases": row.get("aliases", []),
        "msisdns": row.get("msisdns", []),
        "national_ids": row.get("national_ids", []),
        "country": row.get("country"),
        "metadata": row.get("metadata", {}),
    }


# Suppress unused import — UploadFile reserved for future multipart endpoint.
_ = UploadFile

__all__ = ["router"]
