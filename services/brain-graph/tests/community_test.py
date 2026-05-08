from __future__ import annotations

from brain_graph.community import detect_communities
from brain_graph.subgraph import GraphEdge, GraphNode, Subgraph


def _ring(numbers: list[str], wallet: str) -> Subgraph:
    sg = Subgraph(
        nodes=[GraphNode(kind="Number", id=n) for n in numbers]
        + [GraphNode(kind="Wallet", id=wallet)],
    )
    for i, a in enumerate(numbers):
        for b in numbers[i + 1:]:
            sg.edges.append(
                GraphEdge(
                    kind="CALLED",
                    src_kind="Number",
                    src_id=a,
                    dst_kind="Number",
                    dst_id=b,
                )
            )
    return sg


def test_dense_community_detected() -> None:
    sg = _ring(["A", "B", "C", "D", "E"], "W1")
    comms = detect_communities(sg, min_size=3)
    assert len(comms) >= 1
    assert any(len(c.members) >= 3 for c in comms)


def test_no_community_when_below_threshold() -> None:
    sg = _ring(["A", "B"], "W1")
    assert detect_communities(sg, min_size=3) == []
