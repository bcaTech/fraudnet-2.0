"""Graph merge — fold a remote subgraph into the local view.

The local opco runs ring detection over a Subgraph (`brain_graph.subgraph`).
When ring members have edges that cross into a peer opco, we query the
peer for matching hashed identifiers and merge the response into a unified
view. The merged subgraph carries provenance: every node and edge knows
which opco contributed it, and remote-origin nodes are tagged so the local
graph store is never accidentally polluted.

`merged_subgraph_view()` returns a `RemoteSubgraph` that:
  - Carries hashed identifiers as node IDs (never plaintext).
  - Tags every node with `opco` so analysis code can ask "is this node
    cross-opco?" cheaply.
  - Stitches edges across opco boundaries when both endpoints' hashes
    match (one local, one remote, or remote→remote via a shared peer).

This is read-only fusion; nothing is written back to Memgraph. Cross-opco
*persistence* would re-introduce the PII risk and is explicitly out of
scope for the protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fraudnet.federation.protocol import (
    FederationSubgraphResponse,
    RemoteEdge,
    RemoteNode,
)


@dataclass
class RemoteSubgraph:
    """Hashed-identifier subgraph from one opco's federation response."""

    opco: str
    nodes: list[RemoteNode] = field(default_factory=list)
    edges: list[RemoteEdge] = field(default_factory=list)
    truncated: bool = False
    salt_version: str = "v1"


@dataclass
class MergedNode:
    identifier_hash: str
    kind: str
    opco: str               # 'local' | the peer name
    risk_score: float | None = None
    properties: dict[str, object] = field(default_factory=dict)
    contributors: tuple[str, ...] = ()  # all opcos that contributed this node


@dataclass
class MergedEdge:
    src_hash: str
    dst_hash: str
    kind: str
    ts_ms: int
    opco: str               # the opco that observed this edge
    properties: dict[str, object] = field(default_factory=dict)


@dataclass
class MergedView:
    nodes: list[MergedNode] = field(default_factory=list)
    edges: list[MergedEdge] = field(default_factory=list)

    def cross_opco_edges(self) -> list[MergedEdge]:
        """Edges where the two endpoints originate in different opcos.

        These are the candidate seams for cross-opco ring detection."""
        by_id: dict[str, MergedNode] = {n.identifier_hash: n for n in self.nodes}
        return [
            e
            for e in self.edges
            if (src := by_id.get(e.src_hash)) is not None
            and (dst := by_id.get(e.dst_hash)) is not None
            and src.opco != dst.opco
        ]


def merged_subgraph_view(
    *,
    local_nodes: list[RemoteNode],
    local_edges: list[RemoteEdge],
    remote_subgraphs: list[RemoteSubgraph],
) -> MergedView:
    """Combine the local hashed view with one or more remote responses.

    Identifier collisions: if the same hash appears in multiple opcos
    (which means the underlying identifier is the same — usually because
    the salt and the value match), the node is marked `opco='multi'` and
    its `contributors` lists every opco that saw it. Risk score is the max
    across contributors (most-suspicious-wins).
    """
    nodes: dict[str, MergedNode] = {}

    def _record_node(node: RemoteNode, opco: str) -> None:
        existing = nodes.get(node.identifier_hash)
        if existing is None:
            nodes[node.identifier_hash] = MergedNode(
                identifier_hash=node.identifier_hash,
                kind=node.kind,
                opco=opco,
                risk_score=node.risk_score,
                properties=dict(node.properties),
                contributors=(opco,),
            )
            return
        # Identifier seen in multiple opcos → annotate.
        contributors = tuple(sorted({*existing.contributors, opco}))
        new_score = _max_optional(existing.risk_score, node.risk_score)
        nodes[node.identifier_hash] = MergedNode(
            identifier_hash=existing.identifier_hash,
            kind=existing.kind,
            opco="multi",
            risk_score=new_score,
            properties={**existing.properties, **node.properties},
            contributors=contributors,
        )

    for n in local_nodes:
        _record_node(n, opco="local")
    for sg in remote_subgraphs:
        for n in sg.nodes:
            _record_node(n, opco=sg.opco)

    edges: list[MergedEdge] = []
    for e in local_edges:
        edges.append(
            MergedEdge(
                src_hash=e.src_hash,
                dst_hash=e.dst_hash,
                kind=e.kind,
                ts_ms=e.ts_ms,
                opco="local",
                properties=dict(e.properties),
            )
        )
    for sg in remote_subgraphs:
        for e in sg.edges:
            edges.append(
                MergedEdge(
                    src_hash=e.src_hash,
                    dst_hash=e.dst_hash,
                    kind=e.kind,
                    ts_ms=e.ts_ms,
                    opco=sg.opco,
                    properties=dict(e.properties),
                )
            )

    return MergedView(nodes=list(nodes.values()), edges=edges)


def _max_optional(a: float | None, b: float | None) -> float | None:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def remote_subgraph_from_response(
    response: FederationSubgraphResponse, *, opco: str
) -> RemoteSubgraph:
    return RemoteSubgraph(
        opco=opco,
        nodes=list(response.nodes),
        edges=list(response.edges),
        truncated=response.truncated,
        salt_version=response.salt_version,
    )
