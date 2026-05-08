"""brain-agent-fraud routes — agent ranking + per-agent profile."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel

from fraudnet.audit import record, with_purpose
from fraudnet.auth.principal import Principal, Role
from fraudnet.auth.rbac import require_role
from fraudnet.obs import get_logger, metrics_endpoint
from fraudnet.schemas.types import Purpose
from brain_agent_fraud.profile import AgentProfile, ProfileStore

_log = get_logger("brain_agent_fraud.api")


router = APIRouter()


def _profiles(request: Request) -> ProfileStore:
    return request.app.state.profiles  # type: ignore[no-any-return]


def _principal(request: Request) -> Principal:
    p = getattr(request.state, "principal", None)
    if p is None:
        raise HTTPException(status_code=401, detail="auth required")
    return p  # type: ignore[no-any-return]


class AgentSummary(BaseModel):
    agent_id: str
    composite_score: float
    last_seen_ts_ms: int
    pattern_scores: dict[str, float]
    txn_count: int


class AgentDetail(BaseModel):
    agent_id: str
    composite_score: float
    last_seen_ts_ms: int
    pattern_scores: dict[str, float]
    pattern_evidence: dict[str, dict[str, Any]]
    txn_count: int


def _to_summary(p: AgentProfile) -> AgentSummary:
    return AgentSummary(
        agent_id=p.agent_id,
        composite_score=round(p.composite_score, 3),
        last_seen_ts_ms=p.last_seen_ts_ms,
        pattern_scores={k: round(v, 3) for k, v in p.pattern_scores.items()},
        txn_count=p.txn_count,
    )


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
# Agent endpoints
# ---------------------------------------------------------------------------


@router.get("/agents/risk-ranking", response_model=list[AgentSummary])
@require_role(
    Role.NOC_VIEWER, Role.FRAUD_ANALYST, Role.FRAUD_LEAD, Role.FRAUD_MANAGER
)
async def risk_ranking(
    profiles: Annotated[ProfileStore, Depends(_profiles)],
    principal: Annotated[Principal, Depends(_principal)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    min_score: Annotated[float, Query(ge=0.0, le=1.0)] = 0.5,
) -> list[AgentSummary]:
    """Top agents by composite risk score."""
    with with_purpose(Purpose.FRAUD_PREVENTION):
        ranking = profiles.ranking(limit=limit, min_score=min_score)
        await record(
            action="agent_fraud.ranking.read",
            resource_kind="agent_ranking",
            resource_id="all",
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
            metadata={"limit": str(limit), "min_score": str(min_score)},
        )
    return [_to_summary(p) for p in ranking]


@router.get("/agents/{agent_id}/profile", response_model=AgentDetail)
@require_role(
    Role.NOC_VIEWER, Role.FRAUD_ANALYST, Role.FRAUD_LEAD, Role.FRAUD_MANAGER
)
async def agent_profile(
    agent_id: str,
    profiles: Annotated[ProfileStore, Depends(_profiles)],
    principal: Annotated[Principal, Depends(_principal)],
) -> AgentDetail:
    if not agent_id or len(agent_id) > 64:
        raise HTTPException(status_code=400, detail="invalid agent_id")
    profile = profiles.get(agent_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="agent not found")
    with with_purpose(Purpose.FRAUD_PREVENTION):
        await record(
            action="agent_fraud.profile.read",
            resource_kind="agent",
            resource_id=agent_id,
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
        )
    return AgentDetail(
        agent_id=profile.agent_id,
        composite_score=round(profile.composite_score, 3),
        last_seen_ts_ms=profile.last_seen_ts_ms,
        pattern_scores={k: round(v, 3) for k, v in profile.pattern_scores.items()},
        pattern_evidence={k: dict(v) for k, v in profile.pattern_evidence.items()},
        txn_count=profile.txn_count,
    )


@router.get("/agents/commission-anomalies", response_model=list[AgentSummary])
@require_role(Role.FRAUD_ANALYST, Role.FRAUD_LEAD, Role.FRAUD_MANAGER)
async def commission_anomalies(
    profiles: Annotated[ProfileStore, Depends(_profiles)],
    principal: Annotated[Principal, Depends(_principal)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[AgentSummary]:
    """Agents whose composite is dominated by commission_farming."""
    with with_purpose(Purpose.FRAUD_PREVENTION):
        ranking = profiles.commission_anomalies(limit=limit)
        await record(
            action="agent_fraud.commission_anomalies.read",
            resource_kind="agent_ranking",
            resource_id="commission",
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
        )
    return [_to_summary(p) for p in ranking]


__all__ = ["router"]
