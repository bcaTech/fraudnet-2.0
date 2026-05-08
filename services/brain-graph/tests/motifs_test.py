from __future__ import annotations

from brain_graph.motifs import (
    detect_bust_outs,
    detect_device_sim_wallet_fusion,
    detect_mule_chains,
    detect_sim_carousels,
    detect_sms_url_blocklist,
    detect_voice_sms_momo_24h,
    detect_voice_then_momo_30m,
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


def _add_queried(sg: Subgraph, msisdn: str, domain: str, ts_ms: int) -> None:
    sg.edges.append(
        GraphEdge(
            kind="QUERIED",
            src_kind="Number",
            src_id=msisdn,
            dst_kind="Domain",
            dst_id=domain,
            ts_ms=ts_ms,
            properties={"kind": "dns_query"},
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


class TestVoiceThenMomo30m:
    def test_caller_pays_callee_within_30_min(self) -> None:
        sg = _sg()
        t0 = 1_700_000_000_000
        _add_call(sg, "A", "B", t0, dur=120)
        _add_owns(sg, "A", "WA")
        _add_owns(sg, "B", "WB")
        _add_send(sg, "WA", "WB", t0 + 10 * 60_000, amount=50_000)  # 10 min later

        matches = detect_voice_then_momo_30m(sg)
        assert len(matches) == 1
        m = matches[0]
        assert m.motif == "voice_then_momo_30m"
        assert ("Number", "A") in m.members
        assert ("Wallet", "WA") in m.members
        assert ("Wallet", "WB") in m.members
        assert m.evidence["lag_call_to_send_s"] == 600

    def test_no_match_outside_window(self) -> None:
        sg = _sg()
        t0 = 1_700_000_000_000
        _add_call(sg, "A", "B", t0)
        _add_owns(sg, "A", "WA")
        _add_owns(sg, "B", "WB")
        # 45 min later — outside 30 min window
        _add_send(sg, "WA", "WB", t0 + 45 * 60_000)
        assert detect_voice_then_momo_30m(sg) == []

    def test_no_match_when_send_predates_call(self) -> None:
        sg = _sg()
        t0 = 1_700_000_000_000
        _add_call(sg, "A", "B", t0 + 10 * 60_000)
        _add_owns(sg, "A", "WA")
        _add_owns(sg, "B", "WB")
        _add_send(sg, "WA", "WB", t0)  # before the call
        assert detect_voice_then_momo_30m(sg) == []

    def test_no_match_send_to_third_party_wallet(self) -> None:
        sg = _sg()
        t0 = 1_700_000_000_000
        _add_call(sg, "A", "B", t0)
        _add_owns(sg, "A", "WA")
        _add_owns(sg, "B", "WB")
        _add_send(sg, "WA", "WX", t0 + 5 * 60_000)  # to unrelated wallet
        assert detect_voice_then_momo_30m(sg) == []


class TestSmsUrlBlocklist:
    def test_recipient_queries_flagged_domain_after_sms(self) -> None:
        sg = _sg()
        t0 = 1_700_000_000_000
        _add_sms(sg, "A", "B", t0)
        _add_queried(sg, "B", "phish.example.com", t0 + 5 * 60_000)  # 5 min later

        matches = detect_sms_url_blocklist(
            sg, flagged_domains=frozenset({"phish.example.com"})
        )
        assert len(matches) == 1
        m = matches[0]
        assert m.motif == "sms_url_blocklist"
        assert ("Number", "A") in m.members
        assert ("Number", "B") in m.members
        assert ("Domain", "phish.example.com") in m.members

    def test_empty_blocklist_disables_motif(self) -> None:
        sg = _sg()
        t0 = 1_700_000_000_000
        _add_sms(sg, "A", "B", t0)
        _add_queried(sg, "B", "phish.example.com", t0 + 5 * 60_000)
        assert detect_sms_url_blocklist(sg, flagged_domains=frozenset()) == []

    def test_query_outside_window_no_match(self) -> None:
        sg = _sg()
        t0 = 1_700_000_000_000
        _add_sms(sg, "A", "B", t0)
        # 2 hours later — outside the 1h window
        _add_queried(sg, "B", "phish.example.com", t0 + 2 * 60 * 60_000)
        matches = detect_sms_url_blocklist(
            sg, flagged_domains=frozenset({"phish.example.com"})
        )
        assert matches == []

    def test_query_predating_sms_no_match(self) -> None:
        sg = _sg()
        t0 = 1_700_000_000_000
        _add_queried(sg, "B", "phish.example.com", t0)
        _add_sms(sg, "A", "B", t0 + 5 * 60_000)
        matches = detect_sms_url_blocklist(
            sg, flagged_domains=frozenset({"phish.example.com"})
        )
        assert matches == []


class TestDeviceSimWalletFusion:
    def test_two_numbers_one_with_active_wallet_fires(self) -> None:
        sg = _sg()
        for n in ("A", "B"):
            _add_used(sg, n, "IMEI1")
        _add_owns(sg, "A", "WA")
        _add_send(sg, "WA", "WX", 1_700_000_000_000)

        matches = detect_device_sim_wallet_fusion(sg, min_numbers_per_device=2)
        assert len(matches) == 1
        m = matches[0]
        assert m.motif == "device_sim_wallet_fusion"
        assert ("Device", "IMEI1") in m.members
        assert ("Wallet", "WA") in m.members

    def test_no_active_wallet_does_not_fire_when_required(self) -> None:
        sg = _sg()
        for n in ("A", "B"):
            _add_used(sg, n, "IMEI1")
        _add_owns(sg, "A", "WA")  # owns wallet but never SENT
        assert detect_device_sim_wallet_fusion(sg, min_numbers_per_device=2) == []

    def test_can_relax_active_wallet_requirement(self) -> None:
        sg = _sg()
        for n in ("A", "B"):
            _add_used(sg, n, "IMEI1")
        _add_owns(sg, "A", "WA")
        matches = detect_device_sim_wallet_fusion(
            sg, min_numbers_per_device=2, require_active_wallet=False
        )
        assert len(matches) == 1

    def test_solo_user_does_not_fire(self) -> None:
        sg = _sg()
        _add_used(sg, "A", "IMEI1")
        _add_owns(sg, "A", "WA")
        _add_send(sg, "WA", "WX", 1_700_000_000_000)
        assert detect_device_sim_wallet_fusion(sg, min_numbers_per_device=2) == []


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
