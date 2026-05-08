"""brain-agent routes.

POST /investigate/alert/{alert_id}
POST /investigate/ring/{ring_id}
POST /investigate/entity/{kind}/{id}
GET  /investigate/{job_id}

Auth is the same Keycloak realm as api-noc — analysts already
authenticate there. Rate limit is per-analyst (subject claim), not
per-tenant: cost control is at the human user, not the tenant.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import PlainTextResponse, Response

from fraudnet.audit import record, with_purpose
from fraudnet.auth.principal import Principal, Role
from fraudnet.auth.rbac import require_role
from fraudnet.features import FeatureStore
from fraudnet.graph import GraphClient
from fraudnet.obs import counter, get_logger, metrics_endpoint
from fraudnet.schemas.types import Purpose
from brain_agent.agent import InvestigationAgent, Job
from brain_agent.evidence import (
    build_evidence_for_alert,
    build_evidence_for_entity,
    build_evidence_for_ring,
)
from brain_agent.rate_limit import RateLimiter

_log = get_logger("brain_agent.api")

_INVESTIGATIONS = counter(
    "brain_agent_investigations_total",
    "Investigations submitted via the API.",
    labelnames=("target_kind", "outcome"),
)


router = APIRouter()


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def _agent(request: Request) -> InvestigationAgent:
    return request.app.state.agent  # type: ignore[no-any-return]


def _pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pool  # type: ignore[no-any-return]


def _graph(request: Request) -> GraphClient:
    return request.app.state.graph  # type: ignore[no-any-return]


def _features(request: Request) -> FeatureStore:
    return request.app.state.features  # type: ignore[no-any-return]


def _rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter  # type: ignore[no-any-return]


def _principal(request: Request) -> Principal:
    p = getattr(request.state, "principal", None)
    if p is None:
        raise HTTPException(status_code=401, detail="auth required")
    return p  # type: ignore[no-any-return]


async def _enforce_rate_limit(
    principal: Annotated[Principal, Depends(_principal)],
    limiter: Annotated[RateLimiter, Depends(_rate_limiter)],
) -> None:
    if principal.has_role(Role.GROUP_ADMIN):
        # Bypass for incident triage. Audit-logged at the route handler.
        return
    if not await limiter.allow(principal.subject):
        raise HTTPException(
            status_code=429,
            detail="investigation rate limit exceeded (10/hour). Wait or escalate to a fraud lead.",
        )


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
# Investigation endpoints
# ---------------------------------------------------------------------------


@router.post("/investigate/alert/{alert_id}", dependencies=[Depends(_enforce_rate_limit)])
@require_role(Role.FRAUD_ANALYST, Role.FRAUD_LEAD, Role.FRAUD_MANAGER, Role.GROUP_ADMIN)
async def investigate_alert(
    alert_id: UUID,
    agent: Annotated[InvestigationAgent, Depends(_agent)],
    pool: Annotated[asyncpg.Pool, Depends(_pool)],
    graph: Annotated[GraphClient, Depends(_graph)],
    features: Annotated[FeatureStore, Depends(_features)],
    principal: Annotated[Principal, Depends(_principal)],
) -> dict[str, Any]:
    async def _factory():  # noqa: ANN202
        return await build_evidence_for_alert(
            alert_id=alert_id,
            tenant_id=principal.tenant_id,
            pool=pool,
            graph=graph,
            features=features,
        )

    with with_purpose(Purpose.FRAUD_PREVENTION):
        job = await agent.submit(
            analyst_id=principal.subject,
            tenant_id=principal.tenant_id,
            target_kind="alert",
            target_id=str(alert_id),
            evidence_factory=_factory,
        )
        await record(
            action="brain_agent.investigate.alert",
            resource_kind="alert",
            resource_id=str(alert_id),
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
            metadata={
                "job_id": job.job_id,
                "outcome": job.status,
                "bypassed_rate_limit": str(principal.has_role(Role.GROUP_ADMIN)).lower(),
            },
        )
    _INVESTIGATIONS.labels(target_kind="alert", outcome=job.status).inc()
    return _job_response(job)


@router.post("/investigate/ring/{ring_id}", dependencies=[Depends(_enforce_rate_limit)])
@require_role(Role.FRAUD_ANALYST, Role.FRAUD_LEAD, Role.FRAUD_MANAGER, Role.GROUP_ADMIN)
async def investigate_ring(
    ring_id: UUID,
    agent: Annotated[InvestigationAgent, Depends(_agent)],
    pool: Annotated[asyncpg.Pool, Depends(_pool)],
    graph: Annotated[GraphClient, Depends(_graph)],
    features: Annotated[FeatureStore, Depends(_features)],
    principal: Annotated[Principal, Depends(_principal)],
) -> dict[str, Any]:
    async def _factory():  # noqa: ANN202
        return await build_evidence_for_ring(
            ring_id=ring_id,
            tenant_id=principal.tenant_id,
            pool=pool,
            graph=graph,
            features=features,
        )

    with with_purpose(Purpose.FRAUD_PREVENTION):
        job = await agent.submit(
            analyst_id=principal.subject,
            tenant_id=principal.tenant_id,
            target_kind="ring",
            target_id=str(ring_id),
            evidence_factory=_factory,
        )
        await record(
            action="brain_agent.investigate.ring",
            resource_kind="ring",
            resource_id=str(ring_id),
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
            metadata={"job_id": job.job_id, "outcome": job.status},
        )
    _INVESTIGATIONS.labels(target_kind="ring", outcome=job.status).inc()
    return _job_response(job)


@router.post(
    "/investigate/entity/{kind}/{identifier}",
    dependencies=[Depends(_enforce_rate_limit)],
)
@require_role(Role.FRAUD_ANALYST, Role.FRAUD_LEAD, Role.FRAUD_MANAGER, Role.GROUP_ADMIN)
async def investigate_entity(
    kind: Annotated[str, Path(pattern="^(number|wallet|device)$")],
    identifier: str,
    agent: Annotated[InvestigationAgent, Depends(_agent)],
    pool: Annotated[asyncpg.Pool, Depends(_pool)],
    graph: Annotated[GraphClient, Depends(_graph)],
    features: Annotated[FeatureStore, Depends(_features)],
    principal: Annotated[Principal, Depends(_principal)],
) -> dict[str, Any]:
    if not identifier or len(identifier) > 64:
        raise HTTPException(status_code=400, detail="invalid identifier")

    async def _factory():  # noqa: ANN202
        return await build_evidence_for_entity(
            kind=kind,
            identifier=identifier,
            tenant_id=principal.tenant_id,
            pool=pool,
            graph=graph,
            features=features,
        )

    with with_purpose(Purpose.FRAUD_PREVENTION):
        job = await agent.submit(
            analyst_id=principal.subject,
            tenant_id=principal.tenant_id,
            target_kind=kind,
            target_id=identifier,
            evidence_factory=_factory,
        )
        await record(
            action=f"brain_agent.investigate.{kind}",
            resource_kind=kind,
            # The audit log records the redacted form so the trail does
            # not duplicate raw PII outside the controlled audit topic.
            resource_id=job.redacted_target or identifier[:8],
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
            metadata={"job_id": job.job_id, "outcome": job.status},
        )
    _INVESTIGATIONS.labels(target_kind=kind, outcome=job.status).inc()
    return _job_response(job)


@router.get("/investigate/{job_id}")
@require_role(Role.FRAUD_ANALYST, Role.FRAUD_LEAD, Role.FRAUD_MANAGER, Role.GROUP_ADMIN)
async def get_investigation(
    job_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(_principal)],
) -> dict[str, Any]:
    agent: InvestigationAgent = request.app.state.agent
    job = await agent._store.get(job_id)  # noqa: SLF001 — internal facade
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    # Scope: analysts only see their own investigations; FRAUD_LEAD and
    # above can see anyone's. Cross-tenant access is impossible — jobs
    # carry a tenant_id and the principal must match.
    if job.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=404, detail="job not found")
    if (
        job.analyst_id != principal.subject
        and not principal.has_any(
            Role.FRAUD_LEAD, Role.FRAUD_MANAGER, Role.GROUP_ADMIN
        )
    ):
        raise HTTPException(status_code=403, detail="not your investigation")
    return _job_response(job)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _job_response(job: Job) -> dict[str, Any]:
    out: dict[str, Any] = {
        "job_id": job.job_id,
        "status": job.status,
        "target_kind": job.target_kind,
        "redacted_target": job.redacted_target,
        "created_at_ms": job.created_at_ms,
        "updated_at_ms": job.updated_at_ms,
        "not_available": job.not_available,
        "llm_input_tokens": job.llm_input_tokens,
        "llm_output_tokens": job.llm_output_tokens,
        "llm_cache_read_tokens": job.llm_cache_read_tokens,
    }
    if job.report is not None:
        out["report"] = job.report.model_dump()
    if job.error is not None:
        out["error"] = job.error
    return out


__all__ = ["router"]
