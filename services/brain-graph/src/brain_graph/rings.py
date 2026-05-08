"""Ring identification.

Connected components in the fraud subgraph that share infrastructure are
candidate rings. We score each component by:

  - size (member count),
  - shared-device count (USED edges incident on multiple Numbers),
  - shared-wallet flow (SENT edges between members),
  - motif density (motif matches whose members are subset of the component).

A ring is emitted when the composite score crosses a threshold.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import networkx as nx

from brain_graph.motifs import MotifMatch
from brain_graph.subgraph import Subgraph


@dataclass(frozen=True)
class RingCandidate:
    id: str
    type: str  # voice_scam | smishing | mule | mixed
    members: tuple[tuple[str, str], ...]
    composite_score: float
    member_count: int
    shared_device_count: int
    shared_wallet_flow_count: int
    motif_count: int


def identify_rings(
    sg: Subgraph,
    motifs: list[MotifMatch],
    *,
    min_size: int = 3,
    score_threshold: float = 0.55,
) -> list[RingCandidate]:
    """Build connected components from the fraud-relevant projection
    and score each. Returns components above `score_threshold`."""
    g = _projection(sg)
    if g.number_of_nodes() == 0:
        return []
    components = list(nx.connected_components(g))
    motif_index = _index_motifs_by_member(motifs)

    rings: list[RingCandidate] = []
    for idx, comp in enumerate(sorted(components, key=len, reverse=True)):
        if len(comp) < min_size:
            continue
        sub = g.subgraph(comp)
        member_count = sub.number_of_nodes()
        shared_devices = _count_shared_devices(sub)
        shared_flow = _count_shared_flow(sub)
        comp_motifs = {
            (m.motif, m.members)
            for member in comp
            for m in motif_index.get(member, [])
            if all(p in comp for p in m.members)
        }
        motif_count = len(comp_motifs)
        composite = _score(member_count, shared_devices, shared_flow, motif_count)
        if composite < score_threshold:
            continue
        rings.append(
            RingCandidate(
                id=f"r_{idx:04d}",
                type=_classify(comp_motifs),
                members=tuple(sorted(comp)),
                composite_score=composite,
                member_count=member_count,
                shared_device_count=shared_devices,
                shared_wallet_flow_count=shared_flow,
                motif_count=motif_count,
            )
        )
    return rings


def _projection(sg: Subgraph) -> nx.Graph:
    g = nx.Graph()
    for n in sg.nodes:
        if n.kind in ("Ring", "Account"):
            continue
        g.add_node((n.kind, n.id))
    for e in sg.edges:
        if e.kind in ("MEMBER_OF",):
            continue
        a = (e.src_kind, e.src_id)
        b = (e.dst_kind, e.dst_id)
        if a == b:
            continue
        if a not in g or b not in g:
            continue
        g.add_edge(a, b, kind=e.kind)
    return g


def _count_shared_devices(sub: nx.Graph) -> int:
    return sum(
        1 for n, deg in sub.degree() if n[0] == "Device" and deg >= 2
    )


def _count_shared_flow(sub: nx.Graph) -> int:
    return sum(
        1 for u, v, d in sub.edges(data=True) if d.get("kind") == "SENT"
    )


def _index_motifs_by_member(
    motifs: list[MotifMatch],
) -> dict[tuple[str, str], list[MotifMatch]]:
    out: dict[tuple[str, str], list[MotifMatch]] = defaultdict(list)
    for m in motifs:
        for member in m.members:
            out[member].append(m)
    return out


def _classify(motifs: set[tuple[str, tuple]]) -> str:
    """Pick a ring type from the dominant motif. Simple voting."""
    counts: dict[str, int] = defaultdict(int)
    for motif_name, _ in motifs:
        counts[motif_name] += 1
    if not counts:
        return "mixed"
    top = max(counts.items(), key=lambda kv: kv[1])[0]
    return {
        "voice_sms_momo_24h": "mixed",
        "mule_chain": "mule",
        "sim_carousel": "voice_scam",
        "bust_out": "mule",
    }.get(top, "mixed")


def _score(
    members: int, shared_devices: int, shared_flow: int, motif_count: int
) -> float:
    """Heuristic composite score in [0, 1]."""
    size_term = min(0.3, 0.05 * members)
    device_term = min(0.3, 0.1 * shared_devices)
    flow_term = min(0.2, 0.05 * shared_flow)
    motif_term = min(0.4, 0.15 * motif_count)
    return min(0.99, size_term + device_term + flow_term + motif_term)
