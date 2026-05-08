"""api-noc routes.

All write paths and PII reads are wrapped in a `with_purpose(FRAUD_PREVENTION)`
block and emit an audit event via `fraudnet.audit.record()`. RBAC enforced
per route via `@require_role`.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel

from fraudnet.audit import record, with_purpose
from fraudnet.auth.principal import Principal, Role
from fraudnet.auth.rbac import require_role
from fraudnet.graph import GraphClient, GraphScope
from fraudnet.obs import get_logger, metrics_endpoint
from fraudnet.schemas.errors import ConflictError, NotFoundError
from fraudnet.schemas.types import Purpose
from api_noc.db import (
    AlertRepo,
    Database,
    RingRepo,
    TakedownRepo,
    is_valid_transition,
)

_log = get_logger("api_noc.api")


# ----------------------------------------------------------------------
# Dependency injection
# ----------------------------------------------------------------------


def _alerts_repo(request: Request) -> AlertRepo:
    return request.app.state.alerts  # type: ignore[no-any-return]


def _rings_repo(request: Request) -> RingRepo:
    return request.app.state.rings  # type: ignore[no-any-return]


def _takedowns_repo(request: Request) -> TakedownRepo:
    return request.app.state.takedowns  # type: ignore[no-any-return]


def _graph(request: Request) -> GraphClient:
    return request.app.state.graph  # type: ignore[no-any-return]


def _principal(request: Request) -> Principal:
    p = getattr(request.state, "principal", None)
    if p is None:
        raise HTTPException(status_code=401, detail="auth required")
    return p  # type: ignore[no-any-return]


# ----------------------------------------------------------------------
# Schemas (request/response)
# ----------------------------------------------------------------------


class AlertOut(BaseModel):
    id: UUID
    type: str
    severity: str
    subject_kind: str
    subject_id: str
    score: float
    ring_id: UUID | None
    status: str
    assignee_id: UUID | None
    closed_at: Any | None
    closed_reason: str | None
    details: dict[str, Any]
    decision_id: str | None
    created_at: Any
    updated_at: Any


class RingOut(BaseModel):
    id: UUID
    type: str
    status: str
    composite_score: float | None
    active_since: Any
    last_activity: Any
    member_count: int
    metadata: dict[str, Any]


class RingMemberOut(BaseModel):
    member_kind: str
    member_id: str
    role: str | None
    confidence: float | None


class RingDetailOut(BaseModel):
    ring: RingOut
    members: list[RingMemberOut]


class CloseAlertRequest(BaseModel):
    reason: str
    false_positive: bool = False


class CreateTakedownRequest(BaseModel):
    ring_id: UUID
    metadata: dict[str, Any] = {}


class TransitionTakedownRequest(BaseModel):
    target: str
    filed_with: str | None = None


# ----------------------------------------------------------------------
# Router
# ----------------------------------------------------------------------


router = APIRouter()


@router.get("/health/live", include_in_schema=False)
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", include_in_schema=False)
async def readiness(request: Request) -> dict[str, str]:
    db: Database = request.app.state.db
    try:
        async with db.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="db unavailable") from None
    return {"status": "ready"}


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    body, content_type = metrics_endpoint()()
    return PlainTextResponse(body, media_type=content_type)


# ----- Alerts -----


@router.get("/alerts", response_model=list[AlertOut])
@require_role(Role.NOC_VIEWER, Role.FRAUD_ANALYST, Role.FRAUD_LEAD, Role.FRAUD_MANAGER)
async def list_alerts(
    repo: Annotated[AlertRepo, Depends(_alerts_repo)],
    principal: Annotated[Principal, Depends(_principal)],
    status: Annotated[list[str] | None, Query()] = None,
    severity: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AlertOut]:
    with with_purpose(Purpose.FRAUD_PREVENTION):
        rows = await repo.list(
            tenant_id=principal.tenant_id,
            status=status,
            severity=severity,
            limit=limit,
            offset=offset,
        )
        return [AlertOut.model_validate(r) for r in rows]


@router.get("/alerts/{alert_id}", response_model=AlertOut)
@require_role(Role.NOC_VIEWER, Role.FRAUD_ANALYST, Role.FRAUD_LEAD, Role.FRAUD_MANAGER)
async def get_alert(
    alert_id: UUID,
    repo: Annotated[AlertRepo, Depends(_alerts_repo)],
    principal: Annotated[Principal, Depends(_principal)],
) -> AlertOut:
    with with_purpose(Purpose.FRAUD_PREVENTION):
        row = await repo.get(tenant_id=principal.tenant_id, alert_id=alert_id)
        if row is None:
            raise NotFoundError("alert not found")
        await record(
            action="alerts.read",
            resource_kind="alert",
            resource_id=str(alert_id),
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
        )
        return AlertOut.model_validate(row)


@router.post("/alerts/{alert_id}/claim", response_model=AlertOut)
@require_role(Role.FRAUD_ANALYST, Role.FRAUD_LEAD)
async def claim_alert(
    alert_id: UUID,
    repo: Annotated[AlertRepo, Depends(_alerts_repo)],
    principal: Annotated[Principal, Depends(_principal)],
) -> AlertOut:
    with with_purpose(Purpose.FRAUD_PREVENTION):
        # principal.subject is the Keycloak sub; in production users table
        # maps it to a UUID. Phase 1 simplification: we use a UUID built
        # from the Keycloak sub for the test path. Wire to users table in
        # the integration tests.
        from uuid import UUID as _UUID
        try:
            actor_uuid = _UUID(principal.subject)
        except ValueError:
            # Subject is non-UUID (Keycloak default is UUID; tests use
            # arbitrary strings). Hash it deterministically.
            import hashlib
            h = hashlib.sha256(principal.subject.encode()).digest()[:16]
            actor_uuid = _UUID(bytes=h)

        row = await repo.claim(
            tenant_id=principal.tenant_id,
            alert_id=alert_id,
            assignee_id=actor_uuid,
        )
        if row is None:
            raise ConflictError(
                "alert already claimed or not in 'new' status",
                code=None,  # type: ignore[arg-type]
            )
        await record(
            action="alerts.claim",
            resource_kind="alert",
            resource_id=str(alert_id),
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
        )
        return AlertOut.model_validate(row)


@router.post("/alerts/{alert_id}/close", response_model=AlertOut)
@require_role(Role.FRAUD_ANALYST, Role.FRAUD_LEAD)
async def close_alert(
    alert_id: UUID,
    body: CloseAlertRequest,
    repo: Annotated[AlertRepo, Depends(_alerts_repo)],
    principal: Annotated[Principal, Depends(_principal)],
) -> AlertOut:
    with with_purpose(Purpose.FRAUD_PREVENTION):
        from uuid import UUID as _UUID
        try:
            actor_uuid = _UUID(principal.subject)
        except ValueError:
            import hashlib
            actor_uuid = _UUID(bytes=hashlib.sha256(principal.subject.encode()).digest()[:16])

        row = await repo.close(
            tenant_id=principal.tenant_id,
            alert_id=alert_id,
            actor_id=actor_uuid,
            reason=body.reason,
            is_false_positive=body.false_positive,
        )
        if row is None:
            raise NotFoundError("alert not found")
        await record(
            action="alerts.close",
            resource_kind="alert",
            resource_id=str(alert_id),
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
            metadata={"reason": body.reason, "fp": body.false_positive},
        )
        return AlertOut.model_validate(row)


# ----- Rings -----


@router.get("/rings", response_model=list[RingOut])
@require_role(Role.NOC_VIEWER, Role.FRAUD_ANALYST, Role.FRAUD_LEAD, Role.FRAUD_MANAGER)
async def list_rings(
    repo: Annotated[RingRepo, Depends(_rings_repo)],
    principal: Annotated[Principal, Depends(_principal)],
    status: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[RingOut]:
    with with_purpose(Purpose.FRAUD_PREVENTION):
        rows = await repo.list(
            tenant_id=principal.tenant_id, status=status, limit=limit, offset=offset
        )
        return [RingOut.model_validate(r) for r in rows]


@router.get("/rings/{ring_id}", response_model=RingDetailOut)
@require_role(Role.NOC_VIEWER, Role.FRAUD_ANALYST, Role.FRAUD_LEAD, Role.FRAUD_MANAGER)
async def get_ring(
    ring_id: UUID,
    repo: Annotated[RingRepo, Depends(_rings_repo)],
    principal: Annotated[Principal, Depends(_principal)],
) -> RingDetailOut:
    with with_purpose(Purpose.FRAUD_PREVENTION):
        ring, members = await repo.get(tenant_id=principal.tenant_id, ring_id=ring_id)
        if ring is None:
            raise NotFoundError("ring not found")
        await record(
            action="rings.read",
            resource_kind="ring",
            resource_id=str(ring_id),
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
        )
        return RingDetailOut(
            ring=RingOut.model_validate(ring),
            members=[RingMemberOut.model_validate(m) for m in members],
        )


@router.get("/rings/{ring_id}/graph")
@require_role(Role.FRAUD_ANALYST, Role.FRAUD_LEAD, Role.FRAUD_MANAGER)
async def ring_graph(
    ring_id: UUID,
    graph: Annotated[GraphClient, Depends(_graph)],
    principal: Annotated[Principal, Depends(_principal)],
    depth: Annotated[int, Query(ge=1, le=4)] = 2,
    max_nodes: Annotated[int, Query(ge=10, le=1000)] = 200,
) -> dict[str, Any]:
    with with_purpose(Purpose.FRAUD_PREVENTION):
        scope = GraphScope(tenant_id=principal.tenant_id)
        async with graph.session(scope) as session:
            rows = await session.cypher(
                """
                MATCH (r:Ring {ring_id: $ring_id, tenant_id: $tenant_id})
                CALL {
                    WITH r
                    MATCH p = (r)<-[:MEMBER_OF*..1]-(member)-[*1..$depth]-(connected)
                    RETURN p LIMIT $max_nodes
                }
                RETURN p
                """,
                op="ring_graph",
                ring_id=str(ring_id),
                depth=depth,
                max_nodes=max_nodes,
            )
        await record(
            action="rings.graph",
            resource_kind="ring",
            resource_id=str(ring_id),
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
            metadata={"depth": depth, "max_nodes": max_nodes},
        )
        return {"ring_id": str(ring_id), "paths": rows}


# ----- Takedowns -----


@router.post("/takedowns")
@require_role(Role.FRAUD_LEAD, Role.FRAUD_MANAGER)
async def create_takedown(
    body: CreateTakedownRequest,
    repo: Annotated[TakedownRepo, Depends(_takedowns_repo)],
    principal: Annotated[Principal, Depends(_principal)],
) -> dict[str, Any]:
    with with_purpose(Purpose.FRAUD_PREVENTION):
        from uuid import UUID as _UUID
        try:
            actor_uuid = _UUID(principal.subject)
        except ValueError:
            import hashlib
            actor_uuid = _UUID(bytes=hashlib.sha256(principal.subject.encode()).digest()[:16])

        row = await repo.create(
            tenant_id=principal.tenant_id,
            ring_id=body.ring_id,
            created_by=actor_uuid,
            metadata=body.metadata,
        )
        await record(
            action="takedowns.create",
            resource_kind="takedown",
            resource_id=str(row["id"]),
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
            metadata={"ring_id": str(body.ring_id)},
        )
        return row


@router.post("/takedowns/{takedown_id}/transition")
@require_role(Role.FRAUD_LEAD, Role.FRAUD_MANAGER)
async def transition_takedown(
    takedown_id: UUID,
    body: TransitionTakedownRequest,
    repo: Annotated[TakedownRepo, Depends(_takedowns_repo)],
    principal: Annotated[Principal, Depends(_principal)],
) -> dict[str, Any]:
    with with_purpose(Purpose.FRAUD_PREVENTION):
        try:
            row = await repo.transition(
                tenant_id=principal.tenant_id,
                takedown_id=takedown_id,
                target=body.target,
                filed_with=body.filed_with,
            )
        except ValueError as exc:
            from fraudnet.schemas.errors import FraudNetError, ErrorCode

            raise FraudNetError(
                str(exc), code=ErrorCode.CONFLICT
            ) from exc
        if row is None:
            raise NotFoundError("takedown not found")
        await record(
            action="takedowns.transition",
            resource_kind="takedown",
            resource_id=str(takedown_id),
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
            metadata={"target": body.target, "filed_with": body.filed_with or ""},
        )
        return row


# Re-export for tests
__all__ = ["is_valid_transition", "router"]
