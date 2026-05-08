"""Cross-opco detector — exit identification, peer confirmation, motif emission.

The detector talks to peers via FederationClient. We inject a fake client
that records lookups and returns canned responses; no real httpx calls.
"""

from __future__ import annotations

from typing import Any

import pytest

from fraudnet.federation import FederationClient, hash_identifier
from fraudnet.federation.protocol import (
    FederationFlag,
    FederationLookupResponse,
)
from brain_graph.cross_opco import (
    detect_cross_opco_rings,
    find_exit_identifiers,
    to_motif_event,
)
from brain_graph.rings import RingCandidate
from brain_graph.subgraph import GraphEdge, Subgraph


def _ring(*members: tuple[str, str]) -> RingCandidate:
    return RingCandidate(
        id="r_0001",
        type="mule",
        members=members,
        composite_score=0.7,
        member_count=len(members),
        shared_device_count=0,
        shared_wallet_flow_count=1,
        motif_count=2,
    )


def _sg_with_edges(edges: list[GraphEdge]) -> Subgraph:
    return Subgraph(nodes=[], edges=edges)


def _send_edge(src: str, dst: str, ts: int = 1000) -> GraphEdge:
    return GraphEdge(
        kind="SENT",
        src_kind="Wallet",
        src_id=src,
        dst_kind="Wallet",
        dst_id=dst,
        ts_ms=ts,
        properties={"amount": 5000},
    )


def test_find_exits_skips_intra_ring_flow() -> None:
    """A SENT edge whose dst is also a ring member is intra-ring; exits are
    only edges that leave the ring."""
    ring = _ring(("Wallet", "w_a"), ("Wallet", "w_b"))
    sg = _sg_with_edges(
        [
            _send_edge("w_a", "w_b"),  # intra-ring
            _send_edge("w_a", "w_external"),  # exit
        ]
    )
    exits = find_exit_identifiers(sg, ring=ring)
    assert len(exits) == 1
    assert exits[0].plaintext == "w_external"
    assert exits[0].kind == "Wallet"


def test_find_exits_dedupes_by_hash() -> None:
    """Multiple edges to the same external should yield one exit record."""
    ring = _ring(("Wallet", "w_a"))
    sg = _sg_with_edges(
        [
            _send_edge("w_a", "w_external", ts=1000),
            _send_edge("w_a", "w_external", ts=2000),
        ]
    )
    exits = find_exit_identifiers(sg, ring=ring)
    assert len(exits) == 1


class _FakeFederationClient:
    """Stand-in for FederationClient. Records lookups and returns
    canned responses by peer."""

    def __init__(self, responses: dict[str, list[FederationFlag]]) -> None:
        self._responses = responses
        self.lookup_calls: list[tuple[str, list[str]]] = []

    @property
    def peers(self) -> tuple[str, ...]:
        return tuple(self._responses)

    async def lookup_flags(
        self, *, peer: str, identifier_hashes: list[str]
    ) -> FederationLookupResponse:
        self.lookup_calls.append((peer, identifier_hashes))
        matched = [
            f for f in self._responses.get(peer, []) if f.identifier_hash in set(identifier_hashes)
        ]
        return FederationLookupResponse(matched=matched, server_id=peer)


async def test_detect_emits_when_peer_confirms() -> None:
    ring = _ring(("Wallet", "w_a"))
    sg = _sg_with_edges([_send_edge("w_a", "w_external")])
    expected_hash = hash_identifier("w_external", kind="wallet")
    confirming_flag = FederationFlag(
        identifier_hash=expected_hash,
        identifier_kind="wallet",
        indicator_kind="mule",
        confidence=0.9,
        first_seen_ms=0,
        last_seen_ms=0,
        evidence={},
    )
    fed = _FakeFederationClient({"opco-uganda": [confirming_flag]})

    out = await detect_cross_opco_rings(
        rings=[ring], subgraph=sg, federation=fed  # type: ignore[arg-type]
    )
    assert len(out) == 1
    cor = out[0]
    assert cor.ring.id == ring.id
    assert len(cor.confirmations) == 1
    peer_name, flag = cor.confirmations[0]
    assert peer_name == "opco-uganda"
    assert flag.identifier_hash == expected_hash
    # Composite score lifted above the local ring's 0.7
    assert cor.composite_score > 0.7
    # The hashed peer member is on the cross-opco record
    assert ("Wallet", expected_hash) in cor.members_hashed


async def test_detect_skips_when_no_peer_confirmation() -> None:
    """Peer returns empty → no cross-opco ring emitted."""
    ring = _ring(("Wallet", "w_a"))
    sg = _sg_with_edges([_send_edge("w_a", "w_external")])
    fed = _FakeFederationClient({"opco-uganda": []})
    out = await detect_cross_opco_rings(
        rings=[ring], subgraph=sg, federation=fed  # type: ignore[arg-type]
    )
    assert out == []


async def test_detect_no_lookup_when_no_peers() -> None:
    """Federation client with no peers configured short-circuits."""
    ring = _ring(("Wallet", "w_a"))
    sg = _sg_with_edges([_send_edge("w_a", "w_external")])
    fed = _FakeFederationClient({})
    out = await detect_cross_opco_rings(
        rings=[ring], subgraph=sg, federation=fed  # type: ignore[arg-type]
    )
    assert out == []
    assert fed.lookup_calls == []


async def test_detect_no_lookup_when_no_exits() -> None:
    """A ring with no outgoing flow → no peer call (cost control)."""
    ring = _ring(("Wallet", "w_a"), ("Wallet", "w_b"))
    sg = _sg_with_edges([])
    fed = _FakeFederationClient({"opco-uganda": []})
    out = await detect_cross_opco_rings(
        rings=[ring], subgraph=sg, federation=fed  # type: ignore[arg-type]
    )
    assert out == []
    assert fed.lookup_calls == []


async def test_multiple_peers_increase_score() -> None:
    """Two independent peers confirming → higher composite than one."""
    ring = _ring(("Wallet", "w_a"))
    sg = _sg_with_edges([_send_edge("w_a", "w_external")])
    h = hash_identifier("w_external", kind="wallet")
    flag = FederationFlag(
        identifier_hash=h,
        identifier_kind="wallet",
        indicator_kind="mule",
        confidence=0.9,
        first_seen_ms=0,
        last_seen_ms=0,
        evidence={},
    )
    fed_one = _FakeFederationClient({"opco-uganda": [flag]})
    fed_two = _FakeFederationClient(
        {"opco-uganda": [flag], "opco-cameroon": [flag]}
    )

    out_one = await detect_cross_opco_rings(
        rings=[ring], subgraph=sg, federation=fed_one  # type: ignore[arg-type]
    )
    out_two = await detect_cross_opco_rings(
        rings=[ring], subgraph=sg, federation=fed_two  # type: ignore[arg-type]
    )
    assert out_two[0].composite_score >= out_one[0].composite_score


async def test_motif_event_includes_local_and_hashed_members() -> None:
    """The emitted MotifDetectedV1 lists local plaintext + hashed peer ids."""
    ring = _ring(("Wallet", "w_a"))
    sg = _sg_with_edges([_send_edge("w_a", "w_external")])
    h = hash_identifier("w_external", kind="wallet")
    flag = FederationFlag(
        identifier_hash=h,
        identifier_kind="wallet",
        indicator_kind="mule",
        confidence=0.85,
        first_seen_ms=0,
        last_seen_ms=0,
        evidence={},
    )
    fed = _FakeFederationClient({"opco-uganda": [flag]})
    out = await detect_cross_opco_rings(
        rings=[ring], subgraph=sg, federation=fed  # type: ignore[arg-type]
    )
    assert out
    event = to_motif_event(out[0], tenant_id="mtn-ghana")
    assert event.motif == "cross_opco_ring"
    member_ids = {m.id for m in event.members}
    assert "w_a" in member_ids       # local plaintext
    assert h in member_ids           # hashed peer member
    assert event.evidence["distinct_peer_count"] == 1
    assert event.tenant_id == "mtn-ghana"


@pytest.mark.parametrize(
    "edge_kind", ["SENT", "CASHED_OUT_TO"]
)
def test_find_exits_handles_both_flow_kinds(edge_kind: str) -> None:
    ring = _ring(("Wallet", "w_a"))
    edge = GraphEdge(
        kind=edge_kind,
        src_kind="Wallet",
        src_id="w_a",
        dst_kind="Wallet" if edge_kind == "SENT" else "Account",
        dst_id="w_external" if edge_kind == "SENT" else "acct_x",
        ts_ms=1000,
    )
    # Account exits aren't hashed (kind not in _HASH_KIND_BY_NODE) — only
    # the SENT case yields an exit. CASHED_OUT_TO returns 0 here.
    sg = _sg_with_edges([edge])
    exits = find_exit_identifiers(sg, ring=ring)
    expected = 1 if edge_kind == "SENT" else 0
    assert len(exits) == expected
