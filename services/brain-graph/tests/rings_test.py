from __future__ import annotations

from brain_graph.motifs import MotifMatch
from brain_graph.rings import identify_rings
from brain_graph.subgraph import GraphEdge, GraphNode, Subgraph


def test_identifies_dense_ring() -> None:
    sg = Subgraph(
        nodes=[
            GraphNode(kind="Number", id="A"),
            GraphNode(kind="Number", id="B"),
            GraphNode(kind="Number", id="C"),
            GraphNode(kind="Device", id="IMEI1"),
            GraphNode(kind="Wallet", id="W1"),
            GraphNode(kind="Wallet", id="W2"),
        ],
    )
    # Shared device across A,B,C — strong ring signal.
    for n in ("A", "B", "C"):
        sg.edges.append(
            GraphEdge(
                kind="USED",
                src_kind="Number",
                src_id=n,
                dst_kind="Device",
                dst_id="IMEI1",
            )
        )
    sg.edges.append(
        GraphEdge(
            kind="OWNS",
            src_kind="Number",
            src_id="A",
            dst_kind="Wallet",
            dst_id="W1",
        )
    )
    sg.edges.append(
        GraphEdge(
            kind="SENT",
            src_kind="Wallet",
            src_id="W1",
            dst_kind="Wallet",
            dst_id="W2",
            ts_ms=1_700_000_000_000,
            properties={"amount": 100},
        )
    )
    motifs = [
        MotifMatch(
            motif="sim_carousel",
            members=(("Device", "IMEI1"), ("Number", "A"), ("Number", "B"), ("Number", "C")),
            confidence=0.8,
            evidence={"numbers_per_device": 3},
        )
    ]
    rings = identify_rings(sg, motifs, min_size=3, score_threshold=0.0)
    assert len(rings) >= 1
    r0 = rings[0]
    assert r0.member_count >= 3
    assert r0.shared_device_count >= 1
    assert r0.motif_count >= 1
