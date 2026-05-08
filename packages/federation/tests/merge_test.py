"""Merge logic — local-only, single peer, multi-peer, cross-opco edges,
collision behaviour."""

from __future__ import annotations

from fraudnet.federation.merge import (
    RemoteSubgraph,
    merged_subgraph_view,
)
from fraudnet.federation.protocol import RemoteEdge, RemoteNode


def test_local_only_passes_through() -> None:
    nodes = [
        RemoteNode(kind="Number", identifier_hash="a" * 64, risk_score=0.5),
    ]
    edges: list[RemoteEdge] = []
    view = merged_subgraph_view(local_nodes=nodes, local_edges=edges, remote_subgraphs=[])
    assert len(view.nodes) == 1
    assert view.nodes[0].opco == "local"
    assert view.cross_opco_edges() == []


def test_single_peer_tags_origin() -> None:
    """Remote-origin nodes carry their opco; local nodes carry 'local'."""
    local_nodes = [RemoteNode(kind="Number", identifier_hash="a" * 64, risk_score=0.5)]
    remote = RemoteSubgraph(
        opco="opco-uganda",
        nodes=[RemoteNode(kind="Number", identifier_hash="b" * 64, risk_score=0.7)],
        edges=[],
    )
    view = merged_subgraph_view(
        local_nodes=local_nodes, local_edges=[], remote_subgraphs=[remote]
    )
    by_opco = {n.identifier_hash: n.opco for n in view.nodes}
    assert by_opco["a" * 64] == "local"
    assert by_opco["b" * 64] == "opco-uganda"


def test_collision_marks_multi_and_takes_max_score() -> None:
    """If the same hash is reported by multiple opcos, mark as multi-source
    and use the highest risk score."""
    local_nodes = [
        RemoteNode(kind="Number", identifier_hash="x" * 64, risk_score=0.4)
    ]
    remote = RemoteSubgraph(
        opco="opco-uganda",
        nodes=[RemoteNode(kind="Number", identifier_hash="x" * 64, risk_score=0.9)],
        edges=[],
    )
    view = merged_subgraph_view(
        local_nodes=local_nodes, local_edges=[], remote_subgraphs=[remote]
    )
    assert len(view.nodes) == 1
    n = view.nodes[0]
    assert n.opco == "multi"
    assert set(n.contributors) == {"local", "opco-uganda"}
    assert n.risk_score == 0.9


def test_cross_opco_edges_detected() -> None:
    """An edge whose endpoints come from different opcos is the seam we
    care about — that's the cross-opco ring signal."""
    a = "a" * 64
    b = "b" * 64
    local_nodes = [RemoteNode(kind="Wallet", identifier_hash=a)]
    local_edges = [
        RemoteEdge(kind="SENT", src_hash=a, dst_hash=b, ts_ms=10),
    ]
    remote = RemoteSubgraph(
        opco="opco-uganda",
        nodes=[RemoteNode(kind="Wallet", identifier_hash=b)],
        edges=[],
    )
    view = merged_subgraph_view(
        local_nodes=local_nodes, local_edges=local_edges, remote_subgraphs=[remote]
    )
    cross = view.cross_opco_edges()
    assert len(cross) == 1
    assert cross[0].src_hash == a
    assert cross[0].dst_hash == b
