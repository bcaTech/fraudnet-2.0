"""intel-repository routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

from fraudnet.audit import record, with_purpose
from fraudnet.auth.principal import Principal, Role
from fraudnet.auth.rbac import require_role
from fraudnet.obs import get_logger, metrics_endpoint
from fraudnet.schemas.types import Purpose
from intel_repository.cache import CachedIntelRepo, IntelHit
from intel_repository.repo import IntelRepo, VALID_KINDS

_log = get_logger("intel_repository.api")


router = APIRouter()


def _repo(request: Request) -> IntelRepo:
    return request.app.state.repo  # type: ignore[no-any-return]


def _cache(request: Request) -> CachedIntelRepo:
    return request.app.state.cache  # type: ignore[no-any-return]


def _principal(request: Request) -> Principal:
    p = getattr(request.state, "principal", None)
    if p is None:
        raise HTTPException(status_code=401, detail="auth required")
    return p  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class IntelEntryOut(BaseModel):
    kind: str
    identifier: str
    risk_score: float
    hit_count: int
    first_seen_at: Any
    last_seen_at: Any
    expires_at: Any
    metadata: dict[str, Any]
    contributor: str


class IntelPage(BaseModel):
    items: list[IntelEntryOut]
    page: int
    limit: int
    total: int


class ContributeBody(BaseModel):
    kind: str = Field(min_length=1)
    identifier: str = Field(min_length=1, max_length=256)
    risk_score: float = Field(ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    ttl_s: int | None = Field(default=None, ge=60, le=365 * 24 * 3600)


class LookupResponse(BaseModel):
    hit: bool
    score: float
    cache_hit: bool
    metadata: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Health / metrics
# ---------------------------------------------------------------------------


@router.get("/health/live", include_in_schema=False)
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", include_in_schema=False)
async def readiness(repo: Annotated[IntelRepo, Depends(_repo)]) -> dict[str, str]:
    return {"status": "ready" if repo else "starting"}  # type: ignore[truthy-bool]


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    body, content_type = metrics_endpoint()()
    return PlainTextResponse(body, media_type=content_type)


# ---------------------------------------------------------------------------
# Hot lookup — open to service callers; auth-gate at network layer in prod.
# ---------------------------------------------------------------------------


@router.get("/intel/lookup/{kind}/{identifier}", response_model=LookupResponse)
async def lookup(
    kind: Annotated[str, Path()],
    identifier: str,
    cache: Annotated[CachedIntelRepo, Depends(_cache)],
) -> LookupResponse:
    """Sub-millisecond hot-path lookup. Used by brain-* during scoring."""
    if kind not in VALID_KINDS:
        raise HTTPException(status_code=400, detail=f"unknown kind: {kind}")
    if len(identifier) > 256:
        raise HTTPException(status_code=400, detail="identifier too long")
    hit: IntelHit = await cache.lookup(kind=kind, identifier=identifier)
    return LookupResponse(
        hit=hit.hit,
        score=hit.score,
        cache_hit=hit.cache_hit,
        metadata=hit.metadata,
    )


# ---------------------------------------------------------------------------
# Listing endpoints — investigator-facing; require analyst+.
# ---------------------------------------------------------------------------


def _list_endpoint(kind: str):  # noqa: ANN201
    async def _handler(
        repo: Annotated[IntelRepo, Depends(_repo)],
        principal: Annotated[Principal, Depends(_principal)],
        page: Annotated[int, Query(ge=1, le=500)] = 1,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
        min_score: Annotated[float, Query(ge=0.0, le=1.0)] = 0.0,
    ) -> IntelPage:
        with with_purpose(Purpose.FRAUD_PREVENTION):
            rows, total = await repo.list_by_kind(
                kind=kind,
                tenant_id=principal.tenant_id,
                page=page,
                limit=limit,
                min_score=min_score,
            )
            await record(
                action=f"intel.{kind}.read",
                resource_kind="intel_entries",
                resource_id=kind,
                actor_id=principal.subject,
                tenant_id=principal.tenant_id,
                metadata={"page": str(page), "limit": str(limit)},
            )
        return IntelPage(
            items=[IntelEntryOut.model_validate(r) for r in rows],
            page=page,
            limit=limit,
            total=total,
        )

    return _handler


# Each endpoint = its own decorator so the path / role are explicit.
@router.get("/intel/suspect-numbers", response_model=IntelPage)
@require_role(
    Role.NOC_VIEWER, Role.FRAUD_ANALYST, Role.FRAUD_LEAD, Role.FRAUD_MANAGER
)
async def suspect_numbers(
    repo: Annotated[IntelRepo, Depends(_repo)],
    principal: Annotated[Principal, Depends(_principal)],
    page: Annotated[int, Query(ge=1, le=500)] = 1,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    min_score: Annotated[float, Query(ge=0.0, le=1.0)] = 0.0,
) -> IntelPage:
    return await _list_endpoint("suspect_number")(
        repo=repo, principal=principal, page=page, limit=limit, min_score=min_score
    )


@router.get("/intel/high-risk-destinations", response_model=IntelPage)
@require_role(Role.FRAUD_ANALYST, Role.FRAUD_LEAD, Role.FRAUD_MANAGER)
async def high_risk_destinations(
    repo: Annotated[IntelRepo, Depends(_repo)],
    principal: Annotated[Principal, Depends(_principal)],
    page: Annotated[int, Query(ge=1, le=500)] = 1,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    min_score: Annotated[float, Query(ge=0.0, le=1.0)] = 0.0,
) -> IntelPage:
    return await _list_endpoint("high_risk_destination")(
        repo=repo, principal=principal, page=page, limit=limit, min_score=min_score
    )


@router.get("/intel/unallocated-ranges", response_model=IntelPage)
@require_role(Role.FRAUD_ANALYST, Role.FRAUD_LEAD, Role.FRAUD_MANAGER)
async def unallocated_ranges(
    repo: Annotated[IntelRepo, Depends(_repo)],
    principal: Annotated[Principal, Depends(_principal)],
    page: Annotated[int, Query(ge=1, le=500)] = 1,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    min_score: Annotated[float, Query(ge=0.0, le=1.0)] = 0.0,
) -> IntelPage:
    return await _list_endpoint("unallocated_range")(
        repo=repo, principal=principal, page=page, limit=limit, min_score=min_score
    )


@router.get("/intel/scam-templates", response_model=IntelPage)
@require_role(Role.FRAUD_ANALYST, Role.FRAUD_LEAD, Role.FRAUD_MANAGER)
async def scam_templates(
    repo: Annotated[IntelRepo, Depends(_repo)],
    principal: Annotated[Principal, Depends(_principal)],
    page: Annotated[int, Query(ge=1, le=500)] = 1,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    min_score: Annotated[float, Query(ge=0.0, le=1.0)] = 0.0,
) -> IntelPage:
    return await _list_endpoint("scam_template")(
        repo=repo, principal=principal, page=page, limit=limit, min_score=min_score
    )


@router.get("/intel/spoof-indicators", response_model=IntelPage)
@require_role(Role.FRAUD_ANALYST, Role.FRAUD_LEAD, Role.FRAUD_MANAGER)
async def spoof_indicators(
    repo: Annotated[IntelRepo, Depends(_repo)],
    principal: Annotated[Principal, Depends(_principal)],
    page: Annotated[int, Query(ge=1, le=500)] = 1,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    min_score: Annotated[float, Query(ge=0.0, le=1.0)] = 0.0,
) -> IntelPage:
    return await _list_endpoint("spoof_indicator")(
        repo=repo, principal=principal, page=page, limit=limit, min_score=min_score
    )


@router.get("/intel/agent-risk", response_model=IntelPage)
@require_role(Role.FRAUD_ANALYST, Role.FRAUD_LEAD, Role.FRAUD_MANAGER)
async def agent_risk(
    repo: Annotated[IntelRepo, Depends(_repo)],
    principal: Annotated[Principal, Depends(_principal)],
    page: Annotated[int, Query(ge=1, le=500)] = 1,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    min_score: Annotated[float, Query(ge=0.0, le=1.0)] = 0.0,
) -> IntelPage:
    return await _list_endpoint("agent_risk")(
        repo=repo, principal=principal, page=page, limit=limit, min_score=min_score
    )


# ---------------------------------------------------------------------------
# Manual contribute / stats
# ---------------------------------------------------------------------------


@router.post("/intel/contribute", response_model=IntelEntryOut)
@require_role(Role.FRAUD_ANALYST, Role.FRAUD_LEAD, Role.FRAUD_MANAGER)
async def contribute(
    body: ContributeBody,
    repo: Annotated[IntelRepo, Depends(_repo)],
    cache: Annotated[CachedIntelRepo, Depends(_cache)],
    principal: Annotated[Principal, Depends(_principal)],
) -> IntelEntryOut:
    if body.kind not in VALID_KINDS:
        raise HTTPException(status_code=400, detail=f"unknown kind: {body.kind}")
    ttl_s = body.ttl_s or (90 * 24 * 3600)
    with with_purpose(Purpose.FRAUD_PREVENTION):
        row = await repo.upsert_entry(
            kind=body.kind,
            identifier=body.identifier,
            risk_score=body.risk_score,
            ttl_s=ttl_s,
            contributor=f"analyst:{principal.subject}",
            metadata=body.metadata,
            tenant_id=principal.tenant_id,
        )
        await cache.invalidate(
            kind=body.kind, identifier=body.identifier, tenant_id=principal.tenant_id
        )
        await record(
            action="intel.contribute",
            resource_kind="intel_entry",
            resource_id=str(row["id"]),
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
            metadata={"kind": body.kind, "score": str(body.risk_score)},
        )
    return IntelEntryOut.model_validate(row)


@router.get("/intel/stats")
@require_role(
    Role.NOC_VIEWER, Role.FRAUD_ANALYST, Role.FRAUD_LEAD, Role.FRAUD_MANAGER
)
async def stats(
    repo: Annotated[IntelRepo, Depends(_repo)],
    principal: Annotated[Principal, Depends(_principal)],
) -> dict[str, Any]:
    with with_purpose(Purpose.FRAUD_PREVENTION):
        return await repo.stats(tenant_id=principal.tenant_id)


__all__ = ["router"]
