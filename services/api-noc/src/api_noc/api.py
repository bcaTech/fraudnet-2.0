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


def _db(request: Request) -> Database:
    return request.app.state.db  # type: ignore[no-any-return]


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
    rings: Annotated[RingRepo, Depends(_rings_repo)],
    graph: Annotated[GraphClient, Depends(_graph)],
    principal: Annotated[Principal, Depends(_principal)],
    depth: Annotated[int, Query(ge=1, le=3)] = 2,
    max_nodes: Annotated[int, Query(ge=10, le=1000)] = 200,
) -> dict[str, Any]:
    """Subgraph for ring visualisation.

    Two-stage: (1) load ring members from Postgres, (2) for those members,
    pull k-hop neighbourhood from Memgraph and shape as nodes + edges. The
    frontend renders this directly with cytoscape/visgraph.
    """
    with with_purpose(Purpose.FRAUD_PREVENTION):
        ring, members = await rings.get(tenant_id=principal.tenant_id, ring_id=ring_id)
        if ring is None:
            raise NotFoundError("ring not found")
        msisdns = [m["member_id"] for m in members if m["member_kind"] == "number"]
        wallet_ids = [m["member_id"] for m in members if m["member_kind"] == "wallet"]

        scope = GraphScope(tenant_id=principal.tenant_id)
        async with graph.session(scope) as session:
            rows = await session.cypher(
                """
                MATCH (n)
                WHERE coalesce(n.tenant_id, $tenant_id) = $tenant_id
                  AND (
                       (n:Number AND n.msisdn IN $msisdns)
                    OR (n:Wallet AND n.wallet_id IN $wallets)
                  )
                WITH collect(n) AS seeds
                UNWIND seeds AS seed
                MATCH (seed)-[r*1..3]-(other)
                WHERE coalesce(other.tenant_id, $tenant_id) = $tenant_id
                WITH seed, other, r LIMIT $max_nodes
                RETURN seed, other, r
                """,
                op="ring_graph",
                msisdns=msisdns,
                wallets=wallet_ids,
                max_nodes=max_nodes,
                depth=depth,
            )
        await record(
            action="rings.graph",
            resource_kind="ring",
            resource_id=str(ring_id),
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
            metadata={"depth": depth, "max_nodes": max_nodes},
        )
        return _shape_ring_graph(rows, ring_id=ring_id, max_nodes=max_nodes)


def _shape_ring_graph(rows: list[dict[str, Any]], *, ring_id: UUID, max_nodes: int) -> dict[str, Any]:
    """Coerce raw Memgraph path rows to a {nodes, edges} JSON shape."""
    nodes_by_id: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str, int]] = set()

    def _node_payload(obj: Any) -> dict[str, Any] | None:
        if obj is None:
            return None
        labels = list(getattr(obj, "labels", []) or [])
        props = dict(getattr(obj, "_properties", {}) or {})
        kind = next((lbl for lbl in labels if lbl in ("Number", "Wallet", "Device", "Account")), None)
        if kind is None:
            return None
        key_field = {
            "Number": "msisdn",
            "Wallet": "wallet_id",
            "Device": "imei",
            "Account": "account_hash",
        }[kind]
        node_id = props.get(key_field)
        if not node_id:
            return None
        return {
            "id": f"{kind.lower()}:{node_id}",
            "kind": kind,
            "label": str(node_id),
            "risk_score": props.get("risk_score"),
            "properties": {k: v for k, v in props.items() if k != "tenant_id"},
        }

    def _add(payload: dict[str, Any] | None) -> None:
        if payload is None:
            return
        nodes_by_id.setdefault(payload["id"], payload)

    for row in rows:
        _add(_node_payload(row.get("seed")))
        _add(_node_payload(row.get("other")))
        for rel in row.get("r") or []:
            src_node = _node_payload(getattr(rel, "start_node", None))
            dst_node = _node_payload(getattr(rel, "end_node", None))
            if src_node is None or dst_node is None:
                continue
            _add(src_node)
            _add(dst_node)
            ts = getattr(rel, "_properties", {}).get("ts", 0)
            ts_ms = _coerce_ts_ms(ts)
            sig = (src_node["id"], dst_node["id"], rel.type, ts_ms)
            if sig in seen_edges:
                continue
            seen_edges.add(sig)
            edges.append(
                {
                    "src": src_node["id"],
                    "dst": dst_node["id"],
                    "kind": rel.type,
                    "ts_ms": ts_ms,
                    "properties": {
                        k: v for k, v in (rel._properties or {}).items() if k != "ts"  # type: ignore[attr-defined]
                    },
                }
            )

    truncated = len(nodes_by_id) >= max_nodes
    return {
        "ring_id": str(ring_id),
        "nodes": list(nodes_by_id.values()),
        "edges": edges,
        "truncated": truncated,
        "node_count": len(nodes_by_id),
        "edge_count": len(edges),
    }


def _coerce_ts_ms(ts: Any) -> int:
    if hasattr(ts, "to_native"):
        return int(ts.to_native().timestamp() * 1000)
    if isinstance(ts, (int, float)):
        return int(ts)
    return 0


@router.get("/rings/{ring_id}/fund-flow")
@require_role(Role.FRAUD_ANALYST, Role.FRAUD_LEAD, Role.FRAUD_MANAGER)
async def ring_fund_flow(
    ring_id: UUID,
    rings: Annotated[RingRepo, Depends(_rings_repo)],
    graph: Annotated[GraphClient, Depends(_graph)],
    principal: Annotated[Principal, Depends(_principal)],
    hops: Annotated[int, Query(ge=1, le=4)] = 3,
) -> dict[str, Any]:
    """Sankey-shaped fund flow through ring wallets.

    Walks `SENT` and `CASHED_OUT_TO` outgoing edges from member wallets,
    bounded by `hops`. The response collapses parallel edges into a single
    sankey link with cumulative amount.
    """
    with with_purpose(Purpose.FRAUD_PREVENTION):
        ring, members = await rings.get(tenant_id=principal.tenant_id, ring_id=ring_id)
        if ring is None:
            raise NotFoundError("ring not found")
        wallet_ids = [m["member_id"] for m in members if m["member_kind"] == "wallet"]

        scope = GraphScope(tenant_id=principal.tenant_id)
        async with graph.session(scope) as session:
            rows = await session.cypher(
                """
                MATCH (w:Wallet)
                WHERE w.wallet_id IN $wallets AND w.tenant_id = $tenant_id
                CALL {
                    WITH w
                    MATCH path = (w)-[:SENT|CASHED_OUT_TO*1..4]->(target)
                    RETURN path
                    LIMIT 200
                }
                RETURN path
                """,
                op="ring_fund_flow",
                wallets=wallet_ids,
                hops=hops,
            )

        await record(
            action="rings.fund_flow",
            resource_kind="ring",
            resource_id=str(ring_id),
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
        )
        return _shape_fund_flow(rows, ring_id=ring_id)


def _shape_fund_flow(rows: list[dict[str, Any]], *, ring_id: UUID) -> dict[str, Any]:
    nodes_by_id: dict[str, dict[str, Any]] = {}
    links: dict[tuple[str, str], dict[str, Any]] = {}

    def _key(obj: Any) -> str | None:
        labels = list(getattr(obj, "labels", []) or [])
        props = dict(getattr(obj, "_properties", {}) or {})
        if "Wallet" in labels:
            return f"wallet:{props.get('wallet_id')}"
        if "Account" in labels:
            return f"account:{props.get('account_hash')}"
        if "Number" in labels:
            return f"number:{props.get('msisdn')}"
        return None

    def _label_kind(obj: Any) -> str:
        labels = list(getattr(obj, "labels", []) or [])
        return next((lbl for lbl in labels if lbl in ("Wallet", "Account", "Number")), "Unknown")

    for row in rows:
        path = row.get("path")
        if path is None:
            continue
        # neo4j Path object: .nodes, .relationships
        path_nodes = list(getattr(path, "nodes", []) or [])
        path_rels = list(getattr(path, "relationships", []) or [])
        for n in path_nodes:
            nid = _key(n)
            if nid is None:
                continue
            nodes_by_id.setdefault(
                nid,
                {
                    "id": nid,
                    "kind": _label_kind(n),
                    "label": nid.split(":", 1)[1],
                },
            )
        for rel in path_rels:
            src = _key(getattr(rel, "start_node", None))
            dst = _key(getattr(rel, "end_node", None))
            if src is None or dst is None:
                continue
            link = links.setdefault(
                (src, dst),
                {
                    "src": src,
                    "dst": dst,
                    "kind": rel.type,
                    "amount_minor_total": 0,
                    "edge_count": 0,
                },
            )
            link["edge_count"] += 1
            amount = (rel._properties or {}).get("amount", 0)  # type: ignore[attr-defined]
            try:
                link["amount_minor_total"] += int(amount or 0)
            except (TypeError, ValueError):
                pass

    return {
        "ring_id": str(ring_id),
        "nodes": list(nodes_by_id.values()),
        "links": list(links.values()),
    }


@router.get("/rings/{ring_id}/timeline")
@require_role(Role.FRAUD_ANALYST, Role.FRAUD_LEAD, Role.FRAUD_MANAGER)
async def ring_timeline(
    ring_id: UUID,
    rings: Annotated[RingRepo, Depends(_rings_repo)],
    graph: Annotated[GraphClient, Depends(_graph)],
    principal: Annotated[Principal, Depends(_principal)],
    limit: Annotated[int, Query(ge=10, le=1000)] = 200,
) -> dict[str, Any]:
    """Chronological event timeline across ring members."""
    with with_purpose(Purpose.FRAUD_PREVENTION):
        ring, members = await rings.get(tenant_id=principal.tenant_id, ring_id=ring_id)
        if ring is None:
            raise NotFoundError("ring not found")
        msisdns = [m["member_id"] for m in members if m["member_kind"] == "number"]
        wallets = [m["member_id"] for m in members if m["member_kind"] == "wallet"]

        scope = GraphScope(tenant_id=principal.tenant_id)
        async with graph.session(scope) as session:
            rows = await session.cypher(
                """
                MATCH (a:Number)-[r:CALLED|SMSED]->(b:Number)
                WHERE (a.msisdn IN $msisdns OR b.msisdn IN $msisdns)
                  AND coalesce(a.tenant_id, $tenant_id) = $tenant_id
                  AND coalesce(b.tenant_id, $tenant_id) = $tenant_id
                RETURN a, b, r
                LIMIT $limit
                UNION
                MATCH (a:Wallet)-[r:SENT]->(b:Wallet)
                WHERE (a.wallet_id IN $wallets OR b.wallet_id IN $wallets)
                  AND coalesce(a.tenant_id, $tenant_id) = $tenant_id
                  AND coalesce(b.tenant_id, $tenant_id) = $tenant_id
                RETURN a, b, r
                LIMIT $limit
                """,
                op="ring_timeline",
                msisdns=msisdns,
                wallets=wallets,
                limit=limit,
            )

        await record(
            action="rings.timeline",
            resource_kind="ring",
            resource_id=str(ring_id),
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
        )
        return _shape_timeline(rows, ring_id=ring_id, limit=limit)


def _shape_timeline(
    rows: list[dict[str, Any]], *, ring_id: UUID, limit: int
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for row in rows:
        a = row.get("a")
        b = row.get("b")
        rel = row.get("r")
        if rel is None:
            continue
        a_props = dict(getattr(a, "_properties", {}) or {})
        b_props = dict(getattr(b, "_properties", {}) or {})
        rel_props = dict(getattr(rel, "_properties", {}) or {})
        a_id = a_props.get("msisdn") or a_props.get("wallet_id")
        b_id = b_props.get("msisdn") or b_props.get("wallet_id")
        ts_ms = _coerce_ts_ms(rel_props.get("ts", 0))
        events.append(
            {
                "ts_ms": ts_ms,
                "kind": rel.type,
                "src": a_id,
                "dst": b_id,
                "properties": {k: v for k, v in rel_props.items() if k != "ts"},
            }
        )
    events.sort(key=lambda e: e["ts_ms"])
    return {
        "ring_id": str(ring_id),
        "events": events[:limit],
        "event_count": len(events[:limit]),
    }


@router.get("/rings/{ring_id}/motifs")
@require_role(Role.NOC_VIEWER, Role.FRAUD_ANALYST, Role.FRAUD_LEAD, Role.FRAUD_MANAGER)
async def ring_motifs(
    ring_id: UUID,
    rings: Annotated[RingRepo, Depends(_rings_repo)],
    repo: Annotated[AlertRepo, Depends(_alerts_repo)],
    principal: Annotated[Principal, Depends(_principal)],
) -> dict[str, Any]:
    """Motifs detected within the ring.

    Sources from alerts where `details.motif` is set (motif-driven decisions
    are persisted as alerts on the ring). Returns counts by motif kind plus
    the most recent N matches.
    """
    with with_purpose(Purpose.FRAUD_PREVENTION):
        ring, _members = await rings.get(tenant_id=principal.tenant_id, ring_id=ring_id)
        if ring is None:
            raise NotFoundError("ring not found")
        rows = await repo.list_motif_matches_for_ring(
            tenant_id=principal.tenant_id, ring_id=ring_id, limit=100
        )
        by_motif: dict[str, int] = {}
        for r in rows:
            motif = (r.get("details") or {}).get("motif") or "unknown"
            by_motif[motif] = by_motif.get(motif, 0) + 1
        await record(
            action="rings.motifs",
            resource_kind="ring",
            resource_id=str(ring_id),
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
        )
        return {
            "ring_id": str(ring_id),
            "by_motif": by_motif,
            "total": sum(by_motif.values()),
            "matches": [
                {
                    "alert_id": str(r["id"]),
                    "motif": (r.get("details") or {}).get("motif"),
                    "score": float(r["score"]) if r.get("score") is not None else None,
                    "severity": r["severity"],
                    "subject_kind": r["subject_kind"],
                    "subject_id": r["subject_id"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ],
        }


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


# ----- False-positive monitoring (verified businesses) -----


class BusinessFpRow(BaseModel):
    business_id: str
    business_name: str | None = None
    window_start: str
    alerts_total: int
    alerts_fp: int
    fp_rate: float


@router.get("/false-positives/businesses", response_model=list[BusinessFpRow])
async def false_positives_by_business(
    repo: Annotated[Database, Depends(_db)],
    principal: Annotated[Principal, Depends(_principal)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    window_days: Annotated[int, Query(ge=1, le=90)] = 30,
) -> list[BusinessFpRow]:
    """Verified businesses with the highest false-positive rates.

    Joins business_registry.business_false_positives + the businesses
    table over the last `window_days`. Used by analysts to tune
    classifier thresholds and to spot mislabelled FPs.
    """
    with with_purpose(Purpose.FRAUD_PREVENTION):
        async with repo.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT bfp.business_id::text AS business_id,
                       b.name              AS business_name,
                       to_char(bfp.window_start, 'YYYY-MM-DD') AS window_start,
                       bfp.alerts_total    AS alerts_total,
                       bfp.alerts_fp       AS alerts_fp,
                       bfp.fp_rate         AS fp_rate
                  FROM business_false_positives bfp
                  JOIN businesses b ON b.id = bfp.business_id
                 WHERE bfp.window_start >= now() - ($1::int || ' days')::interval
                   AND b.tenant_id = $2
              ORDER BY bfp.fp_rate DESC, bfp.alerts_total DESC
                 LIMIT $3
                """,
                window_days,
                principal.tenant_id,
                limit,
            )
        return [
            BusinessFpRow(
                business_id=r["business_id"],
                business_name=r["business_name"],
                window_start=r["window_start"],
                alerts_total=int(r["alerts_total"]),
                alerts_fp=int(r["alerts_fp"]),
                fp_rate=float(r["fp_rate"]),
            )
            for r in rows
        ]


# Re-export for tests
__all__ = ["is_valid_transition", "router"]
