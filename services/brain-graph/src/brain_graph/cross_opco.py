"""Cross-opco ring detection.

When a local ring's fund flow exits to an MSISDN / wallet that does not
belong to this opco, query the federation protocol to ask peer opcos
whether they have intelligence on the (hashed) external identifier. If
*any* peer confirms the external identifier is flagged for fraud, the
local ring is escalated to cross-opco priority and a `cross_opco_ring`
motif is emitted.

The detector is purely a fan-out over `FederationClient.lookup_flags`.
The hard rule (CLAUDE.md §7.5): no plaintext crosses the boundary; the
detector hashes every external identifier locally before any peer call.

Inputs:
  - The locally-identified rings (from `rings.identify_rings`).
  - The merged subgraph (we need the SENT and CASHED_OUT_TO edges to find
    *exits* — cases where a ring member's fund flow leaves the local
    subgraph).
  - A FederationClient with one or more peers configured.

Outputs:
  - `CrossOpcoRing` records: the local ring + the list of peer opcos that
    confirmed the linkage + the matched flags.
  - These are emitted on `motifs.detected.v1` with `motif='cross_opco_ring'`
    so `decisions` and the NOC API can pick them up. The members list
    includes hashed identifiers from peers (Subject IDs are the hashes;
    the kind is preserved so the NOC frontend can render them as
    `Number(hash)`, `Wallet(hash)`).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from uuid import uuid4

from fraudnet.federation import FederationClient, hash_identifier
from fraudnet.federation.protocol import FederationFlag
from fraudnet.obs import counter, get_logger
from fraudnet.schemas.events import MotifDetectedV1
from fraudnet.schemas.types import EntityKind, RiskScore, Subject

from brain_graph.rings import RingCandidate
from brain_graph.subgraph import GraphEdge, Subgraph

_log = get_logger("brain_graph.cross_opco")

_CROSS_OPCO_RINGS = counter(
    "brain_graph_cross_opco_rings_total",
    "Cross-opco rings emitted.",
    labelnames=("opco",),
)
_FEDERATION_LOOKUPS = counter(
    "brain_graph_federation_lookups_total",
    "Federation lookups initiated by the cross-opco detector.",
    labelnames=("peer", "outcome"),
)


# The kinds of node identifiers we hash for cross-opco lookup. Devices
# don't usually flow as exits, so we restrict to MSISDN + wallet ID.
_HASH_KIND_BY_NODE: dict[str, str] = {
    "Number": "msisdn",
    "Wallet": "wallet",
}


@dataclass(frozen=True)
class ExitIdentifier:
    """An identifier observed exiting a local ring without matching any
    other ring member — a candidate for cross-opco lookup."""

    kind: str        # 'Number' | 'Wallet'
    plaintext: str   # the local plaintext; never crosses the boundary
    identifier_hash: str
    via_edge_kind: str  # 'SENT' | 'CASHED_OUT_TO' — for evidence
    ts_ms: int


@dataclass(frozen=True)
class CrossOpcoRing:
    """A locally-identified ring with at least one peer-confirmed external
    linkage."""

    ring: RingCandidate
    confirmations: tuple[tuple[str, FederationFlag], ...]  # (peer_name, flag)
    exits: tuple[ExitIdentifier, ...]
    composite_score: float
    members_hashed: tuple[tuple[str, str], ...] = field(
        default_factory=tuple
    )  # (kind, hash) for hashed peer members


def find_exit_identifiers(
    sg: Subgraph, *, ring: RingCandidate
) -> list[ExitIdentifier]:
    """Walk the SENT and CASHED_OUT_TO edges out of ring members.

    An identifier is an "exit" when:
      - it is the destination of a SENT or CASHED_OUT_TO edge whose source
        is a ring member, AND
      - the destination is NOT itself a ring member (intra-ring flow is
        not interesting here).
    """
    member_set = set(ring.members)
    exits: list[ExitIdentifier] = []
    for e in _flow_edges(sg):
        src_key = (e.src_kind, e.src_id)
        dst_key = (e.dst_kind, e.dst_id)
        if src_key not in member_set:
            continue
        if dst_key in member_set:
            continue
        kind = e.dst_kind
        if kind not in _HASH_KIND_BY_NODE:
            continue
        h = hash_identifier(e.dst_id, kind=_HASH_KIND_BY_NODE[kind])
        exits.append(
            ExitIdentifier(
                kind=kind,
                plaintext=e.dst_id,
                identifier_hash=h,
                via_edge_kind=e.kind,
                ts_ms=e.ts_ms,
            )
        )
    return _dedupe_exits(exits)


def _flow_edges(sg: Subgraph) -> list[GraphEdge]:
    return sg.edges_of("SENT") + sg.edges_of("CASHED_OUT_TO")


def _dedupe_exits(exits: list[ExitIdentifier]) -> list[ExitIdentifier]:
    seen: set[tuple[str, str]] = set()
    out: list[ExitIdentifier] = []
    for e in exits:
        sig = (e.kind, e.identifier_hash)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(e)
    return out


async def detect_cross_opco_rings(
    *,
    rings: list[RingCandidate],
    subgraph: Subgraph,
    federation: FederationClient,
) -> list[CrossOpcoRing]:
    """For each local ring, find its exits and ask every peer opco
    whether they have intelligence on those (hashed) identifiers.

    A peer confirmation lifts the ring to cross-opco. Multiple peers
    confirming the same exit increases the composite score (capped at
    0.99).
    """
    out: list[CrossOpcoRing] = []
    if not rings or not federation.peers:
        return out

    for ring in rings:
        exits = find_exit_identifiers(subgraph, ring=ring)
        if not exits:
            continue
        confirmations: list[tuple[str, FederationFlag]] = []
        peer_members: set[tuple[str, str]] = set()

        # Bulk-lookup per peer; one network call per peer regardless of
        # how many exits the ring has. Caps protect peer load: ≤ 500
        # hashes per request (enforced by the protocol).
        hash_to_exit: dict[str, ExitIdentifier] = {
            e.identifier_hash: e for e in exits
        }
        hashes = list(hash_to_exit)

        for peer in federation.peers:
            try:
                resp = await federation.lookup_flags(
                    peer=peer, identifier_hashes=hashes[:500]
                )
                _FEDERATION_LOOKUPS.labels(peer=peer, outcome="ok").inc()
            except Exception as exc:  # noqa: BLE001
                _FEDERATION_LOOKUPS.labels(peer=peer, outcome="error").inc()
                _log.warning(
                    "brain_graph.federation.lookup_failed",
                    peer=peer,
                    error=str(exc),
                )
                continue
            for flag in resp.matched:
                confirmations.append((peer, flag))
                exit_match = hash_to_exit.get(flag.identifier_hash)
                if exit_match is not None:
                    peer_members.add((exit_match.kind, flag.identifier_hash))

        if not confirmations:
            continue

        composite = _composite_score(ring=ring, confirmations=confirmations)
        out.append(
            CrossOpcoRing(
                ring=ring,
                confirmations=tuple(confirmations),
                exits=tuple(exits),
                composite_score=composite,
                members_hashed=tuple(sorted(peer_members)),
            )
        )
        # One metric per confirming peer so dashboards show distribution
        # (which peer is contributing most).
        for peer, _flag in confirmations:
            _CROSS_OPCO_RINGS.labels(opco=peer).inc()
    return out


def _composite_score(
    *, ring: RingCandidate, confirmations: list[tuple[str, FederationFlag]]
) -> float:
    """Lift the local ring's composite score by the federation evidence.

    Heuristic: each peer-confirmation adds 0.05 (capped). Multiple
    peers confirming independently is a strong signal — the same fraud
    operator is being seen by multiple opcos.
    """
    distinct_peers = {peer for peer, _ in confirmations}
    peer_lift = min(0.4, 0.1 * len(distinct_peers))
    confidence_lift = min(
        0.2, 0.05 * sum(f.confidence for _, f in confirmations) / max(1, len(confirmations))
    )
    return min(0.99, ring.composite_score + peer_lift + confidence_lift)


def to_motif_event(
    cor: CrossOpcoRing, *, tenant_id: str
) -> MotifDetectedV1:
    """Encode a cross-opco ring as a `motifs.detected.v1` event.

    Members include both local ring members (kind + plaintext id, since
    they are local to this opco) and hashed peer members (kind + hash).
    Downstream `decisions` distinguishes by `id` length: 64 hex chars =
    hashed peer identifier; anything else = local plaintext.
    """
    now_ms = int(time.time() * 1000)
    members: list[Subject] = []
    for kind, member_id in cor.ring.members:
        ent = _node_kind_to_entity(kind)
        if ent is None:
            continue
        members.append(Subject(kind=ent, id=member_id))
    for kind, h in cor.members_hashed:
        ent = _node_kind_to_entity(kind)
        if ent is None:
            continue
        members.append(Subject(kind=ent, id=h))

    distinct_peers = sorted({peer for peer, _ in cor.confirmations})
    evidence: dict[str, str | int | float] = {
        "local_ring_id": cor.ring.id,
        "local_composite_score": cor.ring.composite_score,
        "exit_count": len(cor.exits),
        "confirmation_count": len(cor.confirmations),
        "distinct_peer_count": len(distinct_peers),
        "peers": ",".join(distinct_peers),
    }
    return MotifDetectedV1(
        event_id=f"mot_{uuid4().hex[:24]}",
        event_ts_ms=now_ms,
        ingest_ts_ms=now_ms,
        source="brain-graph:cross-opco",
        tenant_id=tenant_id,
        motif="cross_opco_ring",
        members=members,
        confidence=cor.composite_score,
        score=RiskScore(
            value=cor.composite_score,
            model_id="brain-graph-cross-opco",
            model_version="0.1.0",
            computed_at_ms=now_ms,
        ),
        evidence=evidence,
    )


def _node_kind_to_entity(kind: str) -> EntityKind | None:
    return {
        "Number": EntityKind.NUMBER,
        "Wallet": EntityKind.WALLET,
        "Device": EntityKind.DEVICE,
        "Account": EntityKind.ACCOUNT,
    }.get(kind)
