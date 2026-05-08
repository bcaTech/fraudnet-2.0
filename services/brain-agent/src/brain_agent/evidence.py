"""Evidence collectors.

Three sources, three async fetchers:
  - Postgres: alert + ring + ring_members + prior alerts + prior decisions.
  - Memgraph: k-hop subgraph around the target entity.
  - Aerospike: feature snapshot for the target.

Each collector is independently failure-tolerant — if a source is
unavailable, the missing category is added to `EvidencePackage.not_available`
so the LLM can flag it under `data_gaps`. We never block the
investigation on a single missing source.

PII redaction happens at the boundary: identifiers (msisdn, wallet_id,
imei) are replaced with stable tokens before going into the prompt.
The mapping is held in the request scope so the analyst can still
correlate tokens to alert IDs in the response.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import asyncpg

from fraudnet.audit import with_purpose
from fraudnet.features import FeatureStore, NumberFeatures, WalletFeatures
from fraudnet.graph import GraphClient, GraphScope
from fraudnet.obs import counter, get_logger
from fraudnet.schemas.types import Purpose
from brain_agent.prompt import (
    EvidencePackage,
    redact_account,
    redact_imei,
    redact_msisdn,
    redact_wallet,
)

_log = get_logger("brain_agent.evidence")
_FETCH_FAILED = counter(
    "brain_agent_evidence_fetch_failures_total",
    "Evidence-collector fetch failures by source.",
    labelnames=("source",),
)


_KIND_REDACTOR = {
    "Number": redact_msisdn,
    "Wallet": redact_wallet,
    "Device": redact_imei,
    "Account": redact_account,
    # Frequently used lowercase synonyms in api-noc payloads.
    "number": redact_msisdn,
    "wallet": redact_wallet,
    "device": redact_imei,
}


def _redact(kind: str, value: str) -> str:
    fn = _KIND_REDACTOR.get(kind)
    if fn is None:
        return f"OTHER_{value[:8]}"
    return fn(value)


# ---------------------------------------------------------------------------
# Postgres
# ---------------------------------------------------------------------------


async def fetch_alert(
    pool: asyncpg.Pool, *, alert_id: UUID, tenant_id: str
) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, type, severity, subject_kind, subject_id, score,
                   ring_id, status, details, created_at, updated_at
              FROM alerts
             WHERE id = $1 AND tenant_id = $2
            """,
            alert_id,
            tenant_id,
        )
    return dict(row) if row else None


async def fetch_ring(
    pool: asyncpg.Pool, *, ring_id: UUID, tenant_id: str
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    async with pool.acquire() as conn:
        ring = await conn.fetchrow(
            "SELECT * FROM rings WHERE id = $1 AND tenant_id = $2",
            ring_id,
            tenant_id,
        )
        if ring is None:
            return None, []
        members = await conn.fetch(
            """
            SELECT member_kind, member_id, role, confidence, first_seen, last_seen
              FROM ring_members WHERE ring_id = $1
             ORDER BY confidence DESC NULLS LAST
             LIMIT 50
            """,
            ring_id,
        )
    return dict(ring), [dict(m) for m in members]


async def fetch_prior_alerts_for_subject(
    pool: asyncpg.Pool,
    *,
    subject_kind: str,
    subject_id: str,
    tenant_id: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, type, severity, score, status, created_at,
                   closed_at, closed_reason
              FROM alerts
             WHERE tenant_id = $1 AND subject_kind = $2 AND subject_id = $3
             ORDER BY created_at DESC
             LIMIT $4
            """,
            tenant_id,
            subject_kind,
            subject_id,
            limit,
        )
    return [dict(r) for r in rows]


async def fetch_prior_decisions_for_subject(
    pool: asyncpg.Pool,
    *,
    subject_kind: str,
    subject_id: str,
    tenant_id: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Decisions are stored alongside actions_taken; we surface the
    actuator outcome (tier, action_kind, taken_at) per subject."""
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(
                """
                SELECT id, action_kind, tier, status, taken_at, metadata
                  FROM actions_taken
                 WHERE tenant_id = $1 AND subject_kind = $2 AND subject_id = $3
                 ORDER BY taken_at DESC
                 LIMIT $4
                """,
                tenant_id,
                subject_kind,
                subject_id,
                limit,
            )
        except asyncpg.UndefinedTableError:
            # Migration not applied in this environment; treat as
            # unavailable rather than failing the whole investigation.
            _FETCH_FAILED.labels(source="postgres.actions_taken").inc()
            return []
    return [dict(r) for r in rows]


async def fetch_motif_matches_for_ring(
    pool: asyncpg.Pool, *, ring_id: UUID, tenant_id: str, limit: int = 50
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT details ->> 'motif' AS motif,
                   severity, score, created_at, details
              FROM alerts
             WHERE tenant_id = $1 AND ring_id = $2 AND details ? 'motif'
             ORDER BY created_at DESC
             LIMIT $3
            """,
            tenant_id,
            ring_id,
            limit,
        )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Aerospike features
# ---------------------------------------------------------------------------


async def fetch_features(
    store: FeatureStore, *, kind: str, identifier: str
) -> dict[str, Any] | None:
    """Pull the live feature snapshot for an entity from Aerospike."""
    try:
        if kind in ("number", "Number"):
            snap = await store.get_number(identifier)
            return _features_to_dict(snap) if snap else None
        if kind in ("wallet", "Wallet"):
            snap = await store.get_wallet(identifier)
            return _features_to_dict(snap) if snap else None
    except Exception:  # noqa: BLE001
        _FETCH_FAILED.labels(source="aerospike").inc()
        _log.warning("brain_agent.features.fetch_failed", kind=kind)
        return None
    return None


def _features_to_dict(snap: NumberFeatures | WalletFeatures) -> dict[str, Any]:
    """Coerce a feature dataclass into a JSON-safe dict (no PII fields)."""
    from dataclasses import asdict

    raw = asdict(snap)
    # Strip identifier fields — they are PII and the prompt has the
    # redacted target id already.
    for k in ("msisdn", "wallet_id", "imei"):
        raw.pop(k, None)
    return {
        k: (v.isoformat() if hasattr(v, "isoformat") else v)
        for k, v in raw.items()
    }


# ---------------------------------------------------------------------------
# Memgraph subgraph
# ---------------------------------------------------------------------------


_SUBGRAPH_QUERY = """
MATCH (seed)
WHERE coalesce(seed.tenant_id, $tenant_id) = $tenant_id
  AND (
    (seed:Number AND seed.msisdn = $seed_id) OR
    (seed:Wallet AND seed.wallet_id = $seed_id) OR
    (seed:Device AND seed.imei = $seed_id)
  )
WITH seed
MATCH (seed)-[r*1..2]-(other)
WHERE coalesce(other.tenant_id, $tenant_id) = $tenant_id
WITH seed, other, r LIMIT $max_nodes
RETURN seed, other, r
"""


async def fetch_subgraph(
    graph: GraphClient,
    *,
    kind: str,
    identifier: str,
    tenant_id: str,
    max_nodes: int = 50,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]] | None:
    """Pull the 2-hop neighbourhood around the seed.

    Returns (nodes, edges, summary). Identifiers in the returned dicts
    are *plaintext* — the caller redacts before adding to the prompt.
    """
    scope = GraphScope(tenant_id=tenant_id)
    try:
        with with_purpose(Purpose.FRAUD_PREVENTION):
            async with graph.session(scope) as session:
                rows = await session.cypher(
                    _SUBGRAPH_QUERY,
                    op="brain_agent_subgraph",
                    seed_id=identifier,
                    max_nodes=max_nodes,
                )
    except Exception:  # noqa: BLE001
        _FETCH_FAILED.labels(source="memgraph").inc()
        _log.warning("brain_agent.subgraph.fetch_failed", kind=kind)
        return None
    return _shape_subgraph(rows, max_nodes=max_nodes)


def _shape_subgraph(
    rows: list[dict[str, Any]], *, max_nodes: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    seen_edge: set[tuple[str, str, str, int]] = set()

    def _coerce(node_obj: Any) -> dict[str, Any] | None:
        if node_obj is None:
            return None
        labels = list(getattr(node_obj, "labels", []) or [])
        props = dict(getattr(node_obj, "_properties", {}) or {})
        kind = next(
            (lbl for lbl in labels if lbl in ("Number", "Wallet", "Device", "Account")),
            None,
        )
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
            "kind": kind,
            "plaintext_id": str(node_id),
            "redacted_id": _redact(kind, str(node_id)),
            "risk_score": props.get("risk_score"),
        }

    for row in rows:
        for ref in ("seed", "other"):
            n = _coerce(row.get(ref))
            if n is not None:
                nodes.setdefault(f"{n['kind']}:{n['plaintext_id']}", n)
        for rel in row.get("r") or []:
            src = _coerce(getattr(rel, "start_node", None))
            dst = _coerce(getattr(rel, "end_node", None))
            if src is None or dst is None:
                continue
            ts_obj = (rel._properties or {}).get("ts", 0)  # type: ignore[attr-defined]
            ts_ms = int(ts_obj) if isinstance(ts_obj, (int, float)) else 0
            sig = (src["plaintext_id"], dst["plaintext_id"], rel.type, ts_ms)
            if sig in seen_edge:
                continue
            seen_edge.add(sig)
            edges.append(
                {
                    "kind": rel.type,
                    "src": src["redacted_id"],
                    "dst": dst["redacted_id"],
                    "ts_ms": ts_ms,
                }
            )

    summary = {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "truncated": len(nodes) >= max_nodes,
        "kinds": sorted({n["kind"] for n in nodes.values()}),
    }
    # Strip plaintext from the returned node list before it goes to
    # the prompt; redacted_id is the only safe form.
    redacted_nodes = [
        {"kind": n["kind"], "id": n["redacted_id"], "risk_score": n["risk_score"]}
        for n in nodes.values()
    ]
    return redacted_nodes, edges, summary


# ---------------------------------------------------------------------------
# Top-level evidence builder
# ---------------------------------------------------------------------------


async def build_evidence_for_alert(
    *,
    alert_id: UUID,
    tenant_id: str,
    pool: asyncpg.Pool,
    graph: GraphClient,
    features: FeatureStore,
) -> EvidencePackage:
    not_available: list[str] = []
    alert = await fetch_alert(pool, alert_id=alert_id, tenant_id=tenant_id)
    if alert is None:
        # Empty package, signalled clearly.
        return EvidencePackage(
            target_kind="alert",
            target_id=str(alert_id),
            redacted_target=f"ALERT_{str(alert_id)[:8]}",
            not_available=["alert (not found)"],
        )

    subj_kind = alert["subject_kind"]
    subj_id = alert["subject_id"]
    redacted_target = _redact(subj_kind, subj_id)

    ring: dict[str, Any] | None = None
    ring_members: list[dict[str, Any]] = []
    motif_matches: list[dict[str, Any]] = []
    ring_id = alert.get("ring_id")
    if ring_id is not None:
        ring, ring_members = await fetch_ring(pool, ring_id=ring_id, tenant_id=tenant_id)
        motif_matches = await fetch_motif_matches_for_ring(
            pool, ring_id=ring_id, tenant_id=tenant_id
        )
    else:
        not_available.append("ring (alert is not associated with a ring)")

    prior_alerts, prior_decisions, features_snap, subgraph_result = await asyncio.gather(
        fetch_prior_alerts_for_subject(
            pool, subject_kind=subj_kind, subject_id=subj_id, tenant_id=tenant_id
        ),
        fetch_prior_decisions_for_subject(
            pool, subject_kind=subj_kind, subject_id=subj_id, tenant_id=tenant_id
        ),
        fetch_features(features, kind=subj_kind, identifier=subj_id),
        fetch_subgraph(
            graph, kind=subj_kind, identifier=subj_id, tenant_id=tenant_id
        ),
    )

    feature_snapshots: dict[str, dict[str, Any]] = {}
    if features_snap is not None:
        feature_snapshots[redacted_target] = features_snap
    else:
        not_available.append("feature_snapshots (Aerospike unavailable or empty)")

    subgraph_nodes: list[dict[str, Any]] = []
    subgraph_edges: list[dict[str, Any]] = []
    subgraph_summary: dict[str, Any] | None = None
    if subgraph_result is not None:
        subgraph_nodes, subgraph_edges, subgraph_summary = subgraph_result
    else:
        not_available.append("subgraph (Memgraph unavailable)")

    if not prior_decisions:
        not_available.append("prior_decisions (no prior actuator events)")

    # Strip plaintext subject_id from the alert payload before the LLM
    # sees it; the redacted form lives on `target` already.
    alert_for_prompt = dict(alert)
    alert_for_prompt["subject_id"] = redacted_target
    alert_for_prompt["id"] = str(alert_for_prompt["id"])
    if alert_for_prompt.get("ring_id") is not None:
        alert_for_prompt["ring_id"] = str(alert_for_prompt["ring_id"])

    return EvidencePackage(
        target_kind="alert",
        target_id=str(alert_id),
        redacted_target=redacted_target,
        alert=alert_for_prompt,
        ring=_redact_ring(ring) if ring else None,
        ring_members=[_redact_member(m) for m in ring_members],
        feature_snapshots=feature_snapshots,
        subgraph_summary=subgraph_summary,
        subgraph_nodes=subgraph_nodes,
        subgraph_edges=subgraph_edges,
        prior_alerts=[_redact_alert_summary(a, redacted_target) for a in prior_alerts],
        prior_decisions=[_redact_decision(d, redacted_target) for d in prior_decisions],
        motif_matches=[_redact_motif_match(m) for m in motif_matches],
        not_available=not_available,
    )


async def build_evidence_for_ring(
    *,
    ring_id: UUID,
    tenant_id: str,
    pool: asyncpg.Pool,
    graph: GraphClient,
    features: FeatureStore,
) -> EvidencePackage:
    not_available: list[str] = []
    ring, members = await fetch_ring(pool, ring_id=ring_id, tenant_id=tenant_id)
    redacted_target = f"RING_{str(ring_id)[:8]}"
    if ring is None:
        return EvidencePackage(
            target_kind="ring",
            target_id=str(ring_id),
            redacted_target=redacted_target,
            not_available=["ring (not found)"],
        )

    motif_matches = await fetch_motif_matches_for_ring(
        pool, ring_id=ring_id, tenant_id=tenant_id
    )

    # Pull subgraph + features for the highest-confidence member as the
    # representative sample; investigating the entire ring graph in one
    # LLM call is not cost-effective. The analyst can drill into specific
    # members via the entity endpoint.
    subgraph_nodes: list[dict[str, Any]] = []
    subgraph_edges: list[dict[str, Any]] = []
    subgraph_summary: dict[str, Any] | None = None
    feature_snapshots: dict[str, dict[str, Any]] = {}

    if members:
        rep = members[0]
        rep_kind = rep["member_kind"]
        rep_id = rep["member_id"]
        sg = await fetch_subgraph(
            graph, kind=rep_kind, identifier=rep_id, tenant_id=tenant_id
        )
        if sg is not None:
            subgraph_nodes, subgraph_edges, subgraph_summary = sg
        feats = await fetch_features(features, kind=rep_kind, identifier=rep_id)
        if feats is not None:
            feature_snapshots[_redact(rep_kind, rep_id)] = feats

    if not motif_matches:
        not_available.append("motif_matches (no motif-driven alerts on this ring)")
    if not feature_snapshots:
        not_available.append("feature_snapshots (no representative member or store empty)")

    return EvidencePackage(
        target_kind="ring",
        target_id=str(ring_id),
        redacted_target=redacted_target,
        ring=_redact_ring(ring),
        ring_members=[_redact_member(m) for m in members],
        feature_snapshots=feature_snapshots,
        subgraph_summary=subgraph_summary,
        subgraph_nodes=subgraph_nodes,
        subgraph_edges=subgraph_edges,
        motif_matches=[_redact_motif_match(m) for m in motif_matches],
        not_available=not_available,
    )


async def build_evidence_for_entity(
    *,
    kind: str,
    identifier: str,
    tenant_id: str,
    pool: asyncpg.Pool,
    graph: GraphClient,
    features: FeatureStore,
) -> EvidencePackage:
    not_available: list[str] = []
    redacted_target = _redact(kind, identifier)

    prior_alerts, prior_decisions, features_snap, subgraph_result = await asyncio.gather(
        fetch_prior_alerts_for_subject(
            pool, subject_kind=kind, subject_id=identifier, tenant_id=tenant_id
        ),
        fetch_prior_decisions_for_subject(
            pool, subject_kind=kind, subject_id=identifier, tenant_id=tenant_id
        ),
        fetch_features(features, kind=kind, identifier=identifier),
        fetch_subgraph(
            graph, kind=kind, identifier=identifier, tenant_id=tenant_id
        ),
    )

    feature_snapshots: dict[str, dict[str, Any]] = {}
    if features_snap is not None:
        feature_snapshots[redacted_target] = features_snap
    else:
        not_available.append("feature_snapshots (Aerospike empty or unavailable)")

    subgraph_nodes: list[dict[str, Any]] = []
    subgraph_edges: list[dict[str, Any]] = []
    subgraph_summary: dict[str, Any] | None = None
    if subgraph_result is not None:
        subgraph_nodes, subgraph_edges, subgraph_summary = subgraph_result
    else:
        not_available.append("subgraph (Memgraph unavailable)")
    if not prior_alerts:
        not_available.append("prior_alerts (no historical alerts on this entity)")

    return EvidencePackage(
        target_kind=kind.lower(),
        target_id=identifier,
        redacted_target=redacted_target,
        feature_snapshots=feature_snapshots,
        subgraph_summary=subgraph_summary,
        subgraph_nodes=subgraph_nodes,
        subgraph_edges=subgraph_edges,
        prior_alerts=[_redact_alert_summary(a, redacted_target) for a in prior_alerts],
        prior_decisions=[_redact_decision(d, redacted_target) for d in prior_decisions],
        not_available=not_available,
    )


# ---------------------------------------------------------------------------
# Per-payload redactors
# ---------------------------------------------------------------------------


def _redact_ring(ring: dict[str, Any]) -> dict[str, Any]:
    out = dict(ring)
    out["id"] = f"RING_{str(out['id'])[:8]}"
    return out


def _redact_member(member: dict[str, Any]) -> dict[str, Any]:
    out = dict(member)
    out["member_id"] = _redact(out["member_kind"], out["member_id"])
    return out


def _redact_alert_summary(alert: dict[str, Any], redacted_subject: str) -> dict[str, Any]:
    return {
        "id": str(alert["id"])[:8],
        "type": alert["type"],
        "severity": alert["severity"],
        "score": float(alert["score"]) if alert.get("score") is not None else None,
        "status": alert["status"],
        "created_at": alert["created_at"],
        "closed_at": alert.get("closed_at"),
        "closed_reason": alert.get("closed_reason"),
        "subject": redacted_subject,
    }


def _redact_decision(decision: dict[str, Any], redacted_subject: str) -> dict[str, Any]:
    return {
        "id": str(decision["id"])[:8],
        "action_kind": decision["action_kind"],
        "tier": decision.get("tier"),
        "status": decision["status"],
        "taken_at": decision["taken_at"],
        "subject": redacted_subject,
    }


def _redact_motif_match(motif: dict[str, Any]) -> dict[str, Any]:
    out = dict(motif)
    # `details` may carry plaintext member IDs; drop the field.
    out.pop("details", None)
    return out
