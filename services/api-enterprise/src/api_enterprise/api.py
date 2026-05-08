"""api-enterprise routes.

Tenant-scoped routes live under /tenant/*. Group-level routes (cross-tenant
analytics) live under /group/* and require Role.GROUP_ADMIN. Tenant
provisioning lives under /admin/tenants and requires SYSTEM_ADMIN + step-up.

Every PII-bearing read happens inside `with_purpose(FRAUD_PREVENTION)` and
emits an audit event via `fraudnet.audit.record()`. Every write also emits.

Tenant isolation: the `_principal` dependency yields a Principal whose
`tenant_id` is the verified tenant slug from Keycloak. Every downstream
query carries that slug. Cross-tenant access via this layer is impossible
unless the principal also holds GROUP_ADMIN — and those routes do not take a
tenant slug.
"""

from __future__ import annotations

import hashlib
from time import time
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

from fraudnet.audit import record, with_purpose
from fraudnet.auth.principal import Principal, Role
from fraudnet.auth.rbac import require_role, require_step_up
from fraudnet.federation import FederationClient, hash_identifier
from fraudnet.graph import GraphClient, GraphScope
from fraudnet.kafka import AvroProducer
from fraudnet.obs import counter, get_logger, metrics_endpoint
from fraudnet.schemas.events import IntelEventV1
from fraudnet.schemas.types import EntityKind, Purpose
from api_enterprise.db import (
    BlockRequestRepo,
    Database,
    EnterpriseAlertRepo,
    GroupAnalyticsRepo,
    SharedFlagRepo,
    TenantRepo,
)
from api_enterprise.rate_limit import RateLimiter

_log = get_logger("api_enterprise.api")

_REPORTS = counter(
    "api_enterprise_reports_total",
    "B2B fraud intelligence reports submitted.",
    labelnames=("tenant_id", "indicator_kind"),
)
_BLOCK_REQUESTS = counter(
    "api_enterprise_block_requests_total",
    "B2B cross-network block requests submitted.",
    labelnames=("tenant_id", "target_kind"),
)


router = APIRouter()


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def _alerts_repo(request: Request) -> EnterpriseAlertRepo:
    return request.app.state.alerts  # type: ignore[no-any-return]


def _shared_repo(request: Request) -> SharedFlagRepo:
    return request.app.state.shared  # type: ignore[no-any-return]


def _block_repo(request: Request) -> BlockRequestRepo:
    return request.app.state.blocks  # type: ignore[no-any-return]


def _tenant_repo(request: Request) -> TenantRepo:
    return request.app.state.tenants  # type: ignore[no-any-return]


def _group_repo(request: Request) -> GroupAnalyticsRepo:
    return request.app.state.group  # type: ignore[no-any-return]


def _db(request: Request) -> Database:
    return request.app.state.db  # type: ignore[no-any-return]


def _graph(request: Request) -> GraphClient:
    return request.app.state.graph  # type: ignore[no-any-return]


def _federation(request: Request) -> FederationClient | None:
    """Federation client. None when no peers are configured for this opco."""
    return getattr(request.app.state, "federation", None)


def _rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter  # type: ignore[no-any-return]


def _intel_producer(request: Request) -> AvroProducer[IntelEventV1]:
    return request.app.state.intel_producer  # type: ignore[no-any-return]


def _principal(request: Request) -> Principal:
    p = getattr(request.state, "principal", None)
    if p is None:
        raise HTTPException(status_code=401, detail="auth required")
    return p  # type: ignore[no-any-return]


async def _rate_limit(
    principal: Annotated[Principal, Depends(_principal)],
    limiter: Annotated[RateLimiter, Depends(_rate_limiter)],
) -> None:
    """Per-tenant token bucket. Group-admin paths bypass the per-tenant key
    by using a shared `_group` bucket so a single admin call doesn't drain
    a tenant's quota."""
    key = principal.tenant_id if not principal.has_role(Role.GROUP_ADMIN) else "_group"
    if not await limiter.allow(key):
        raise HTTPException(status_code=429, detail="rate limit exceeded")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TenantAlertOut(BaseModel):
    id: UUID
    type: str
    severity: str
    subject_kind: str
    subject_id: str
    score: float
    ring_id: UUID | None
    status: str
    details: dict[str, Any]
    created_at: Any
    updated_at: Any


class TenantDashboard(BaseModel):
    tenant_id: str
    open_alerts: int
    recent_24h: int
    recent_7d: int
    by_severity: dict[str, int]
    blocked_24h: int


class ReportFraudBody(BaseModel):
    indicator_kind: str   # 'number' | 'wallet' | 'url' | 'imei'
    indicator: str
    kind: str             # 'voice_scam' | 'smishing' | 'mule' | ...
    notes: str | None = None
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)


class BlockRequestBody(BaseModel):
    target_kind: str      # 'msisdn' | 'wallet' | 'url' | 'imei'
    target_value: str
    reason: str
    share_with: list[str] = Field(default_factory=list)  # peer-tenant slugs


class SharedFlagOut(BaseModel):
    id: UUID
    sender_tenant: str
    recipient_tenant: str
    identifier_kind: str
    identifier_hash: str
    indicator_kind: str
    confidence: float
    evidence: dict[str, Any]
    shared_at: Any
    expires_at: Any


class BlockRequestOut(BaseModel):
    id: UUID
    tenant_slug: str
    target_kind: str
    target_value: str
    reason: str
    status: str
    requested_at: Any
    decided_at: Any | None
    decision_notes: str | None


class CreateTenantBody(BaseModel):
    slug: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=2, max_length=200)
    contact_email: str
    federation_enabled: bool = False
    rate_limit_capacity: int = Field(default=60, ge=1, le=10_000)
    rate_limit_refill_per_s: float = Field(default=10.0, ge=0.1, le=10_000)


class TenantOut(BaseModel):
    id: UUID
    slug: str
    name: str
    status: str
    federation_enabled: bool
    rate_limit_capacity: int
    rate_limit_refill_per_s: float
    contact_email: str | None
    created_at: Any
    updated_at: Any


# ---------------------------------------------------------------------------
# Health / metrics
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# /tenant/* — tenant-scoped, ENTERPRISE_USER + ENTERPRISE_ADMIN
# ---------------------------------------------------------------------------


@router.get(
    "/tenant/dashboard",
    response_model=TenantDashboard,
    dependencies=[Depends(_rate_limit)],
)
@require_role(Role.ENTERPRISE_USER, Role.ENTERPRISE_ADMIN)
async def tenant_dashboard(
    repo: Annotated[EnterpriseAlertRepo, Depends(_alerts_repo)],
    principal: Annotated[Principal, Depends(_principal)],
) -> TenantDashboard:
    with with_purpose(Purpose.FRAUD_PREVENTION):
        data = await repo.dashboard(tenant_id=principal.tenant_id)
        await record(
            action="enterprise.dashboard.read",
            resource_kind="tenant",
            resource_id=principal.tenant_id,
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
        )
    return TenantDashboard(tenant_id=principal.tenant_id, **data)


@router.get(
    "/tenant/alerts",
    response_model=list[TenantAlertOut],
    dependencies=[Depends(_rate_limit)],
)
@require_role(Role.ENTERPRISE_USER, Role.ENTERPRISE_ADMIN)
async def tenant_alerts(
    repo: Annotated[EnterpriseAlertRepo, Depends(_alerts_repo)],
    principal: Annotated[Principal, Depends(_principal)],
    status: Annotated[list[str] | None, Query()] = None,
    severity: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[TenantAlertOut]:
    with with_purpose(Purpose.FRAUD_PREVENTION):
        rows = await repo.list(
            tenant_id=principal.tenant_id,
            status=status,
            severity=severity,
            limit=limit,
            offset=offset,
        )
        await record(
            action="enterprise.alerts.read",
            resource_kind="tenant",
            resource_id=principal.tenant_id,
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
            metadata={"limit": str(limit), "offset": str(offset)},
        )
    return [TenantAlertOut.model_validate(r) for r in rows]


@router.post(
    "/tenant/report",
    dependencies=[Depends(_rate_limit)],
)
@require_role(Role.ENTERPRISE_USER, Role.ENTERPRISE_ADMIN)
async def tenant_report(
    body: ReportFraudBody,
    intel: Annotated[AvroProducer[IntelEventV1], Depends(_intel_producer)],
    principal: Annotated[Principal, Depends(_principal)],
) -> dict[str, str]:
    if body.indicator_kind not in {"number", "wallet", "url", "imei"}:
        raise HTTPException(status_code=400, detail="unsupported indicator_kind")
    indicator = body.indicator.strip()
    if not indicator:
        raise HTTPException(status_code=400, detail="indicator is required")
    if len(indicator) > 2_048:
        raise HTTPException(status_code=400, detail="indicator too long")

    now_ms = int(time() * 1000)
    event = IntelEventV1(
        event_id=f"int_{uuid4().hex[:24]}",
        event_ts_ms=now_ms,
        ingest_ts_ms=now_ms,
        source=f"api-enterprise:{principal.tenant_id}",
        tenant_id=principal.tenant_id,
        kind="enterprise_report",
        indicator_kind=EntityKind(body.indicator_kind),
        indicator=indicator,
        confidence=body.confidence,
        attribution=f"enterprise:{principal.tenant_id}:{principal.subject}",
        notes=body.notes,
    )
    await intel.send(event, key=indicator)
    _REPORTS.labels(
        tenant_id=principal.tenant_id, indicator_kind=body.indicator_kind
    ).inc()

    with with_purpose(Purpose.FRAUD_PREVENTION):
        await record(
            action="enterprise.report",
            resource_kind="indicator",
            resource_id=indicator[:128],
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
            metadata={"kind": body.kind, "indicator_kind": body.indicator_kind},
        )
    return {"status": "received", "event_id": event.event_id}


@router.get(
    "/tenant/shared-flags",
    response_model=list[SharedFlagOut],
    dependencies=[Depends(_rate_limit)],
)
@require_role(Role.ENTERPRISE_USER, Role.ENTERPRISE_ADMIN)
async def tenant_shared_flags(
    repo: Annotated[SharedFlagRepo, Depends(_shared_repo)],
    principal: Annotated[Principal, Depends(_principal)],
    direction: Annotated[str, Query(pattern="^(incoming|outgoing|all)$")] = "all",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[SharedFlagOut]:
    with with_purpose(Purpose.FRAUD_PREVENTION):
        rows = await repo.list_for_tenant(
            tenant_id=principal.tenant_id,
            direction=direction,
            limit=limit,
        )
        await record(
            action="enterprise.shared_flags.read",
            resource_kind="tenant",
            resource_id=principal.tenant_id,
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
            metadata={"direction": direction},
        )
    return [SharedFlagOut.model_validate(r) for r in rows]


@router.post(
    "/tenant/block-request",
    response_model=BlockRequestOut,
    dependencies=[Depends(_rate_limit)],
)
@require_role(Role.ENTERPRISE_ADMIN)
async def tenant_block_request(
    body: BlockRequestBody,
    block_repo: Annotated[BlockRequestRepo, Depends(_block_repo)],
    shared_repo: Annotated[SharedFlagRepo, Depends(_shared_repo)],
    federation: Annotated[FederationClient | None, Depends(_federation)],
    principal: Annotated[Principal, Depends(_principal)],
) -> BlockRequestOut:
    if body.target_kind not in {"msisdn", "wallet", "url", "imei"}:
        raise HTTPException(status_code=400, detail="unsupported target_kind")
    target = body.target_value.strip()
    if not target:
        raise HTTPException(status_code=400, detail="target_value is required")
    if len(body.reason) < 8:
        raise HTTPException(
            status_code=400, detail="reason must be at least 8 characters"
        )

    actor_uuid = _principal_to_uuid(principal)
    with with_purpose(Purpose.FRAUD_PREVENTION):
        row = await block_repo.submit(
            tenant_id=principal.tenant_id,
            target_kind=body.target_kind,
            target_value=target,
            reason=body.reason,
            requested_by=actor_uuid,
        )

        # Optionally share the indicator (hashed) with peer tenants. This is
        # the federation hand-off: the local block request enters the NOC
        # review queue, while peer opcos get a hashed flag they can match
        # against their own subscriber base without seeing the plaintext.
        for peer in body.share_with:
            ident_hash = hash_identifier(target, kind=body.target_kind)
            await shared_repo.submit(
                sender_tenant=principal.tenant_id,
                recipient_tenant=peer,
                identifier_kind=body.target_kind,
                identifier_hash=ident_hash,
                indicator_kind="block_request",
                confidence=0.85,
                evidence={"reason": body.reason[:512]},
            )
            if federation is not None and peer in federation.peers:
                try:
                    await federation.publish_flag(
                        peer=peer,
                        identifier_hash=ident_hash,
                        identifier_kind=body.target_kind,
                        indicator_kind="block_request",
                        confidence=0.85,
                    )
                except Exception as exc:  # noqa: BLE001
                    # Federation push is best-effort; the local block request
                    # has already been recorded so the NOC will pick it up
                    # regardless. We log + continue so a peer outage does
                    # not break the local user flow.
                    _log.warning(
                        "enterprise.federation.publish_failed",
                        peer=peer,
                        error=str(exc),
                    )

        await record(
            action="enterprise.block_request",
            resource_kind=body.target_kind,
            resource_id=target[:128],
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
            metadata={
                "target_kind": body.target_kind,
                "shared_with_count": str(len(body.share_with)),
            },
        )
    _BLOCK_REQUESTS.labels(
        tenant_id=principal.tenant_id, target_kind=body.target_kind
    ).inc()
    return BlockRequestOut.model_validate(row)


# ---------------------------------------------------------------------------
# /group/* — cross-tenant analytics (GROUP_ADMIN only).
# ---------------------------------------------------------------------------


@router.get("/group/overview", dependencies=[Depends(_rate_limit)])
@require_role(Role.GROUP_ADMIN)
async def group_overview(
    repo: Annotated[GroupAnalyticsRepo, Depends(_group_repo)],
    principal: Annotated[Principal, Depends(_principal)],
    include_by_opco: Annotated[bool, Query()] = True,
) -> dict[str, Any]:
    """Group-wide fraud KPIs.

    `include_by_opco` (default true) appends a per-tenant breakdown of the
    same headline metrics. Useful for the group dashboard which needs
    both the rolled-up and the per-opco view in one call.
    """
    with with_purpose(Purpose.FRAUD_PREVENTION):
        out = await repo.overview()
        if include_by_opco:
            out["by_opco"] = await repo.overview_by_opco()
        await record(
            action="group.overview.read",
            resource_kind="group",
            resource_id="mtn-group",
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
            metadata={"include_by_opco": str(include_by_opco).lower()},
        )
    return out


@router.get("/group/cross-opco-rings", dependencies=[Depends(_rate_limit)])
@require_role(Role.GROUP_ADMIN)
async def group_cross_opco_rings(
    repo: Annotated[GroupAnalyticsRepo, Depends(_group_repo)],
    principal: Annotated[Principal, Depends(_principal)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    peer: Annotated[str | None, Query()] = None,
    status: Annotated[list[str] | None, Query()] = None,
) -> dict[str, Any]:
    """Rings whose membership crosses opcos.

    Filter by `peer` to scope to rings involving a specific peer opco; by
    `status` to scope to monitoring/takedown/dismantled. Status defaults
    to `monitoring` + `takedown` for the dashboard view; pass
    `?status=dismantled` to inspect closed cases.
    """
    with with_purpose(Purpose.FRAUD_PREVENTION):
        rings = await repo.cross_opco_rings(limit=limit, peer=peer, status=status)
        await record(
            action="group.cross_opco_rings.read",
            resource_kind="group",
            resource_id="mtn-group",
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
            metadata={"peer": peer or "", "limit": str(limit)},
        )
    return {"rings": rings, "count": len(rings), "peer": peer}


@router.get("/group/trending-motifs", dependencies=[Depends(_rate_limit)])
@require_role(Role.GROUP_ADMIN)
async def group_trending_motifs(
    repo: Annotated[GroupAnalyticsRepo, Depends(_group_repo)],
    principal: Annotated[Principal, Depends(_principal)],
    window_hours: Annotated[int, Query(ge=1, le=168)] = 24,
    by_opco: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    """Motif patterns trending across the group.

    Default rolls up across opcos. `by_opco=true` appends a per-opco
    breakdown so the dashboard can show heatmaps (motif × opco)."""
    with with_purpose(Purpose.FRAUD_PREVENTION):
        motifs = await repo.trending_motifs(window_hours=window_hours)
        per_opco: list[dict[str, Any]] = []
        if by_opco:
            per_opco = await repo.trending_motifs_by_opco(window_hours=window_hours)
        await record(
            action="group.trending_motifs.read",
            resource_kind="group",
            resource_id="mtn-group",
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
            metadata={
                "window_hours": str(window_hours),
                "by_opco": str(by_opco).lower(),
            },
        )
    out: dict[str, Any] = {"window_hours": window_hours, "motifs": motifs}
    if by_opco:
        out["by_opco"] = per_opco
    return out


@router.get("/group/shared-flag-volume", dependencies=[Depends(_rate_limit)])
@require_role(Role.GROUP_ADMIN)
async def group_shared_flag_volume(
    repo: Annotated[GroupAnalyticsRepo, Depends(_group_repo)],
    principal: Annotated[Principal, Depends(_principal)],
    window_hours: Annotated[int, Query(ge=1, le=720)] = 168,
) -> dict[str, Any]:
    """Volume of cross-opco intelligence flowing between tenants.

    Used to monitor federation health — a tenant sharing zero outbound
    flags over the last week is either inactive or has a bug.
    """
    with with_purpose(Purpose.FRAUD_PREVENTION):
        rows = await repo.shared_flag_volume(window_hours=window_hours)
        await record(
            action="group.shared_flag_volume.read",
            resource_kind="group",
            resource_id="mtn-group",
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
            metadata={"window_hours": str(window_hours)},
        )
    return {"window_hours": window_hours, "edges": rows, "edge_count": len(rows)}


# ---------------------------------------------------------------------------
# /admin/tenants — tenant provisioning (SYSTEM_ADMIN, step-up required).
# ---------------------------------------------------------------------------


@router.post("/admin/tenants", response_model=TenantOut)
@require_step_up()
@require_role(Role.SYSTEM_ADMIN)
async def create_tenant(
    body: CreateTenantBody,
    repo: Annotated[TenantRepo, Depends(_tenant_repo)],
    graph: Annotated[GraphClient, Depends(_graph)],
    principal: Annotated[Principal, Depends(_principal)],
) -> TenantOut:
    if not _valid_slug(body.slug):
        raise HTTPException(
            status_code=400,
            detail="slug must be lowercase alphanumeric with hyphens",
        )
    if await repo.get(tenant_id=body.slug) is not None:
        raise HTTPException(status_code=409, detail="tenant slug already exists")

    with with_purpose(Purpose.FRAUD_PREVENTION):
        row = await repo.create(
            slug=body.slug,
            name=body.name,
            contact_email=body.contact_email,
            federation_enabled=body.federation_enabled,
            rate_limit_capacity=body.rate_limit_capacity,
            rate_limit_refill_per_s=body.rate_limit_refill_per_s,
        )

        # Provision a tenant root in the graph so all subsequent writes
        # carry the tenant marker. Memgraph queries scope on tenant_id;
        # the :Tenant node is the anchor for cross-tenant audits.
        async with graph.session(GraphScope(tenant_id=body.slug)) as session:
            await session.cypher(
                """
                MERGE (t:Tenant {slug: $slug, tenant_id: $tenant_id})
                ON CREATE SET t.created_at = timestamp(),
                              t.federation_enabled = $fed
                RETURN t.slug AS slug
                """,
                op="provision_tenant",
                slug=body.slug,
                fed=body.federation_enabled,
            )

        await record(
            action="admin.tenant.create",
            resource_kind="tenant",
            resource_id=body.slug,
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
            metadata={
                "name": body.name,
                "federation_enabled": str(body.federation_enabled),
            },
        )
    return TenantOut.model_validate(row)


@router.get("/admin/tenants", response_model=list[TenantOut])
@require_role(Role.SYSTEM_ADMIN)
async def list_tenants(
    repo: Annotated[TenantRepo, Depends(_tenant_repo)],
    principal: Annotated[Principal, Depends(_principal)],
) -> list[TenantOut]:
    with with_purpose(Purpose.FRAUD_PREVENTION):
        rows = await repo.list()
        await record(
            action="admin.tenants.list",
            resource_kind="tenants",
            resource_id="all",
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
        )
    return [TenantOut.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SLUG_RE = __import__("re").compile(r"^[a-z][a-z0-9-]{1,63}$")


def _valid_slug(slug: str) -> bool:
    return bool(_SLUG_RE.match(slug))


def _principal_to_uuid(principal: Principal) -> UUID:
    """Map a Keycloak `sub` to a UUID. UUID subs pass through; non-UUID subs
    are hashed deterministically (matches api-noc's convention)."""
    try:
        return UUID(principal.subject)
    except ValueError:
        digest = hashlib.sha256(principal.subject.encode()).digest()[:16]
        return UUID(bytes=digest)


# Re-export for tests
__all__ = ["router"]
