"""Tests for the api-noc graph-shape helpers.

The endpoint coercion functions are pure: feed them rows that look like
neo4j driver objects (or simple stubs with the same attribute surface)
and assert the JSON shape.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

from api_noc.api import _shape_fund_flow, _shape_ring_graph, _shape_timeline


def _node(labels: list[str], **props):
    return SimpleNamespace(labels=labels, _properties=props)


def _rel(rel_type: str, start_node, end_node, **props):
    return SimpleNamespace(
        type=rel_type,
        start_node=start_node,
        end_node=end_node,
        _properties=props,
    )


def test_shape_ring_graph_emits_nodes_and_edges() -> None:
    a = _node(["Number"], msisdn="+233241000001", risk_score=0.8)
    b = _node(["Number"], msisdn="+233241000002")
    rel = _rel("CALLED", a, b, ts=1_700_000_000_000, duration=30)
    rows = [{"seed": a, "other": b, "r": [rel]}]
    out = _shape_ring_graph(rows, ring_id=UUID(int=1), max_nodes=200)
    assert {n["id"] for n in out["nodes"]} == {"number:+233241000001", "number:+233241000002"}
    assert len(out["edges"]) == 1
    assert out["edges"][0]["kind"] == "CALLED"
    assert out["edges"][0]["ts_ms"] == 1_700_000_000_000


def test_shape_ring_graph_dedupes_repeated_nodes_and_edges() -> None:
    a = _node(["Number"], msisdn="+233241000001")
    b = _node(["Number"], msisdn="+233241000002")
    rel = _rel("CALLED", a, b, ts=1, duration=30)
    rows = [
        {"seed": a, "other": b, "r": [rel]},
        {"seed": a, "other": b, "r": [rel]},
    ]
    out = _shape_ring_graph(rows, ring_id=uuid4(), max_nodes=200)
    assert len(out["nodes"]) == 2
    assert len(out["edges"]) == 1


def test_shape_fund_flow_aggregates_amounts() -> None:
    src = _node(["Wallet"], wallet_id="W1")
    mid = _node(["Wallet"], wallet_id="W2")
    dst = _node(["Wallet"], wallet_id="W3")
    rels = [
        _rel("SENT", src, mid, ts=1, amount=100),
        _rel("SENT", src, mid, ts=2, amount=250),
        _rel("SENT", mid, dst, ts=3, amount=300),
    ]
    path = SimpleNamespace(nodes=[src, mid, dst], relationships=rels)
    out = _shape_fund_flow([{"path": path}], ring_id=uuid4())
    by_pair = {(l["src"], l["dst"]): l for l in out["links"]}
    assert by_pair[("wallet:W1", "wallet:W2")]["amount_minor_total"] == 350
    assert by_pair[("wallet:W1", "wallet:W2")]["edge_count"] == 2
    assert by_pair[("wallet:W2", "wallet:W3")]["amount_minor_total"] == 300


def test_shape_timeline_sorts_by_ts() -> None:
    a = _node(["Number"], msisdn="+233241000001")
    b = _node(["Number"], msisdn="+233241000002")
    r1 = _rel("CALLED", a, b, ts=2_000)
    r2 = _rel("SMSED", a, b, ts=1_000)
    rows = [
        {"a": a, "b": b, "r": r1},
        {"a": a, "b": b, "r": r2},
    ]
    out = _shape_timeline(rows, ring_id=uuid4(), limit=100)
    assert [e["ts_ms"] for e in out["events"]] == [1_000, 2_000]
    assert [e["kind"] for e in out["events"]] == ["SMSED", "CALLED"]
