"""Community detection using Louvain over a weighted projection of the
fraud subgraph.

We run on the undirected projection where edge weight = count of distinct
relationships (CALLED, SMSED, USED, etc.) between two entities. The
graph is in-memory networkx; for the production-scale graph this is fine
at the 15-minute cadence — typical active-window components fit in tens
of thousands of nodes.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import networkx as nx
from networkx.algorithms.community import louvain_communities

from brain_graph.subgraph import GraphNode, Subgraph


@dataclass(frozen=True)
class Community:
    id: str
    members: tuple[tuple[str, str], ...]  # (kind, id) tuples
    size: int
    cohesion: float  # mean intra-community edge weight, normalised


def detect_communities(
    sg: Subgraph,
    *,
    min_size: int = 3,
    resolution: float = 1.0,
    seed: int = 42,
) -> list[Community]:
    """Run Louvain on the weighted projection. Returns communities of
    size >= min_size, ordered by descending size."""
    g = _project(sg)
    if g.number_of_nodes() == 0:
        return []
    raw = louvain_communities(g, resolution=resolution, seed=seed)
    out: list[Community] = []
    for i, members in enumerate(sorted(raw, key=len, reverse=True)):
        if len(members) < min_size:
            continue
        cohesion = _cohesion(g, members)
        out.append(
            Community(
                id=f"c_{i:04d}",
                members=tuple(sorted(members)),
                size=len(members),
                cohesion=cohesion,
            )
        )
    return out


def _project(sg: Subgraph) -> nx.Graph:
    g = nx.Graph()
    for node in sg.nodes:
        if node.kind in ("Ring",):
            continue
        g.add_node((node.kind, node.id))
    weights: dict[tuple[tuple[str, str], tuple[str, str]], int] = defaultdict(int)
    for e in sg.edges:
        if e.kind == "MEMBER_OF":
            continue
        a = (e.src_kind, e.src_id)
        b = (e.dst_kind, e.dst_id)
        if a == b:
            continue
        key = (a, b) if a < b else (b, a)
        weights[key] += 1
    for (a, b), w in weights.items():
        if a not in g or b not in g:
            continue
        g.add_edge(a, b, weight=w)
    return g


def _cohesion(g: nx.Graph, members: set[GraphNode]) -> float:
    sub = g.subgraph(members)
    n = sub.number_of_nodes()
    if n < 2:
        return 0.0
    max_edges = n * (n - 1) / 2
    if max_edges == 0:
        return 0.0
    total_w = sum(d.get("weight", 1) for _, _, d in sub.edges(data=True))
    return float(total_w / max_edges)
