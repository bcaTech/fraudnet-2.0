from __future__ import annotations

from brain_graph.motifs import (
    detect_bust_outs,
    detect_mule_chains,
    detect_sim_carousels,
    detect_voice_sms_momo_24h,
)
from brain_graph.subgraph import GraphEdge, GraphNode, Subgraph


def _sg() -> Subgraph:
    return Subgraph()


def _add_call(sg: Subgraph, a: str, b: str, ts_ms: int, dur: int = 30) -> None:
    sg.edges.append(
        GraphEdge(
            kind="CALLED",
            src_kind="Number",
            src_id=a,
            dst_kind="Number",
            dst_id=b,
            ts_ms=ts_ms,
            properties={"duration": dur},
        )
    )


def _add_sms(sg: Subgraph, a: str, b: str, ts_ms: int) -> None:
    sg.edges.append(
        GraphEdge(
            kind="SMSED",
            src_kind="Number",
            src_id=a,
            dst_kind="Number",
            dst_id=b,
            ts_ms=ts_ms,
            properties={"template_hash": "th_x"},
        )
    )


def _add_owns(sg: Subgraph, msisdn: str, wallet: str) -> None:
    sg.edges.append(
        GraphEdge(
            kind="OWNS",
            src_kind="Number",
            src_id=msisdn,
            dst_kind="Wallet",
            dst_id=wallet,
        )
    )


def _add_send(sg: Subgraph, src: str, dst: str, ts_ms: int, amount: int = 100) -> None:
    sg.edges.append(
        GraphEdge(
            kind="SENT",
            src_kind="Wallet",
            src_id=src,
            dst_kind="Wallet",
            dst_id=dst,
            ts_ms=ts_ms,
            properties={"amount": amount},
        )
    )


def _add_used(sg: Subgraph, msisdn: str, imei: str) -> None:
    sg.edges.append(
        GraphEdge(
            kind="USED",
            src_kind="Number",
            src_id=msisdn,
            dst_kind="Device",
            dst_id=imei,
        )
    )


class TestVoiceSmsMomo24h:
    def test_matches_chain(self) -> None:
        sg = _sg()
        sg.nodes = [
            GraphNode(kind="Number", id="A"),
            GraphNode(kind="Number", id="B"),
            GraphNode(kind="Wallet", id="WB"),
            GraphNode(kind="Wallet", id="WX"),
        ]
        t0 = 1_700_000_000_000
        _add_call(sg, "A", "B", t0)
        _add_sms(sg, "A", "B", t0 + 60_000)            # 1 min later
        _add_owns(sg, "B", "WB")
        _add_send(sg, "WB", "WX", t0 + 60_000 + 60 * 60_000)  # 1 hour later

        matches = detect_voice_sms_momo_24h(sg)
        assert len(matches) == 1
        assert matches[0].motif == "voice_sms_momo_24h"
        assert ("Number", "A") in matches[0].members
        assert ("Wallet", "WB") in matches[0].members

    def test_no_match_when_send_too_late(self) -> None:
        sg = _sg()
        t0 = 1_700_000_000_000
        _add_call(sg, "A", "B", t0)
        _add_sms(sg, "A", "B", t0 + 60_000)
        _add_owns(sg, "B", "WB")
        # Send 25 hours after SMS — out of window.
        _add_send(sg, "WB", "WX", t0 + 60_000 + 25 * 60 * 60_000)
        assert detect_voice_sms_momo_24h(sg) == []


class TestMuleChain:
    def test_finds_three_hop_chain(self) -> None:
        sg = _sg()
        t = 1_700_000_000_000
        _add_send(sg, "W1", "W2", t, 100)
        _add_send(sg, "W2", "W3", t + 1_000, 90)
        _add_send(sg, "W3", "W4", t + 2_000, 80)
        matches = detect_mule_chains(sg, min_length=3)
        assert any(m.motif == "mule_chain" and len(m.members) >= 3 for m in matches)


class TestSimCarousel:
    def test_three_numbers_sharing_device(self) -> None:
        sg = _sg()
        for n in ("A", "B", "C"):
            _add_used(sg, n, "IMEI1")
        matches = detect_sim_carousels(sg, min_numbers_per_device=3)
        assert len(matches) == 1
        assert ("Device", "IMEI1") in matches[0].members

    def test_two_numbers_below_threshold(self) -> None:
        sg = _sg()
        for n in ("A", "B"):
            _add_used(sg, n, "IMEI1")
        assert detect_sim_carousels(sg, min_numbers_per_device=3) == []


class TestBustOut:
    def test_dormant_then_burst(self) -> None:
        sg = _sg()
        latest = 1_700_000_000_000
        # Dormancy: 1 small txn 20 days back
        _add_send(sg, "WD", "WX", latest - 20 * 24 * 60 * 60 * 1000, 50)
        # Burst: 5 high-value within 24h
        for i in range(5):
            _add_send(sg, "WD", f"WT{i}", latest - i * 60_000, 50_000)
        matches = detect_bust_outs(sg)
        assert any(m.motif == "bust_out" for m in matches)
