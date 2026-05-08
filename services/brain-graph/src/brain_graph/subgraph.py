"""Subgraph extraction.

Pulls an active-window slice of the Memgraph fraud graph as plain Python
dataclasses so analysis is independent of the driver. The streaming graph
is the source of truth; this is a snapshot for batch analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal


NodeKind = Literal[
    "Number", "Wallet", "Device", "Account", "Ring", "Domain", "IPEndpoint"
]
EdgeKind = Literal[
    "CALLED",
    "SMSED",
    "SENT",
    "OWNS",
    "USED",
    "CASHED_OUT_TO",
    "MEMBER_OF",
    "QUERIED",
    "CONNECTED",
    "RESOLVED_TO",
]


@dataclass(frozen=True)
class GraphNode:
    kind: NodeKind
    id: str
    properties: dict[str, str | int | float | bool] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphEdge:
    kind: EdgeKind
    src_kind: NodeKind
    src_id: str
    dst_kind: NodeKind
    dst_id: str
    ts_ms: int = 0
    properties: dict[str, str | int | float | bool] = field(default_factory=dict)


@dataclass
class Subgraph:
    """Materialised subgraph: list of nodes + edges. Indices are dense and
    keyed on (kind, id) tuples."""

    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)

    def node_keys(self) -> Iterable[tuple[NodeKind, str]]:
        return ((n.kind, n.id) for n in self.nodes)

    def by_kind(self, kind: NodeKind) -> list[GraphNode]:
        return [n for n in self.nodes if n.kind == kind]

    def edges_of(self, kind: EdgeKind) -> list[GraphEdge]:
        return [e for e in self.edges if e.kind == kind]


# Cypher used to extract a window. Memgraph's datetime() supports ISO; the
# window is bounded in absolute milliseconds so it's deterministic for tests.
_EXTRACT_QUERY = """
MATCH (n)
WHERE (n:Number OR n:Wallet OR n:Device OR n:Account)
  AND coalesce(n.tenant_id, $tenant_id) = $tenant_id
WITH n LIMIT $max_nodes
OPTIONAL MATCH (n)-[r]-(m)
WHERE coalesce(r.ts, $window_floor_ms) >= $window_floor_ms
RETURN n, r, m
"""


async def extract_window(
    session,  # fraudnet.graph._Session
    *,
    tenant_id: str,
    window_floor_ms: int,
    max_nodes: int,
) -> Subgraph:
    """Pull a (best-effort) recent slice of the graph.

    The query intentionally over-pulls — node-only matches that have no
    recent edges still join via OPTIONAL MATCH so isolated nodes survive
    in the snapshot. Component analysis benefits from including isolates.
    """
    rows = await session.cypher(
        _EXTRACT_QUERY,
        op="brain_graph_extract",
        tenant_id=tenant_id,
        window_floor_ms=window_floor_ms,
        max_nodes=max_nodes,
    )
    return _parse_rows(rows)


def _parse_rows(rows: list[dict[str, object]]) -> Subgraph:
    sg = Subgraph()
    seen_nodes: set[tuple[NodeKind, str]] = set()
    seen_edges: set[tuple[str, str, str, int]] = set()

    for row in rows:
        n_obj = row.get("n")
        m_obj = row.get("m")
        r_obj = row.get("r")
        if n_obj is not None:
            node = _coerce_node(n_obj)
            if node and (node.kind, node.id) not in seen_nodes:
                sg.nodes.append(node)
                seen_nodes.add((node.kind, node.id))
        if m_obj is not None:
            other = _coerce_node(m_obj)
            if other and (other.kind, other.id) not in seen_nodes:
                sg.nodes.append(other)
                seen_nodes.add((other.kind, other.id))
        if r_obj is not None and n_obj is not None and m_obj is not None:
            edge = _coerce_edge(n_obj, r_obj, m_obj)
            if edge is None:
                continue
            sig = (edge.src_id, edge.dst_id, edge.kind, edge.ts_ms)
            if sig in seen_edges:
                continue
            seen_edges.add(sig)
            sg.edges.append(edge)
    return sg


_NODE_KEY_BY_KIND: dict[NodeKind, str] = {
    "Number": "msisdn",
    "Wallet": "wallet_id",
    "Device": "imei",
    "Account": "account_hash",
    "Ring": "ring_id",
    "Domain": "fqdn",
    "IPEndpoint": "ip",
}


def _coerce_node(obj: object) -> GraphNode | None:
    """neo4j driver returns Node objects with .labels, ._properties; tests
    pass plain dicts. Both are supported."""
    labels = getattr(obj, "labels", None)
    props = getattr(obj, "_properties", None)
    if labels is None or props is None:
        if isinstance(obj, dict):
            labels = obj.get("labels", [])
            props = {k: v for k, v in obj.items() if k != "labels"}
        else:
            return None
    kind = next((lbl for lbl in labels if lbl in _NODE_KEY_BY_KIND), None)
    if kind is None:
        return None
    key = _NODE_KEY_BY_KIND[kind]  # type: ignore[index]
    node_id = props.get(key)
    if not node_id:
        return None
    return GraphNode(kind=kind, id=str(node_id), properties=dict(props))  # type: ignore[arg-type]


def _coerce_edge(n_obj: object, r_obj: object, m_obj: object) -> GraphEdge | None:
    src = _coerce_node(n_obj)
    dst = _coerce_node(m_obj)
    if src is None or dst is None:
        return None
    rel_type = getattr(r_obj, "type", None) or (
        r_obj.get("type") if isinstance(r_obj, dict) else None
    )
    if rel_type is None:
        return None
    props = getattr(r_obj, "_properties", None)
    if props is None and isinstance(r_obj, dict):
        props = {k: v for k, v in r_obj.items() if k != "type"}
    props = dict(props or {})
    ts = props.get("ts", 0)
    if hasattr(ts, "to_native"):
        ts_ms = int(ts.to_native().timestamp() * 1000)
    elif isinstance(ts, (int, float)):
        ts_ms = int(ts)
    else:
        ts_ms = 0
    return GraphEdge(
        kind=rel_type,  # type: ignore[arg-type]
        src_kind=src.kind,
        src_id=src.id,
        dst_kind=dst.kind,
        dst_id=dst.id,
        ts_ms=ts_ms,
        properties=props,
    )
