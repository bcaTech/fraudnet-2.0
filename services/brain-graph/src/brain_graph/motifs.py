"""Motif detection.

The motifs that matter for the moat (CLAUDE.md §6.2):

  - voice_sms_momo_24h: A→B voice call, then A→B SMS within 1h, then B's
    wallet sends to a counterparty within 24h. The structural fingerprint
    of the threat profile that defines FraudNet's IP.
  - mule_chain: linear fund flow through 3+ wallets.
  - sim_carousel: A→B→C→A device swap chains (shared IMEIs across distinct
    MSISDNs cycling).
  - bust_out: dormant wallet (<= dormancy_floor txns over the lookback)
    suddenly active with a high-value cash-out cluster.

Detection is over an extracted Subgraph. All thresholds are tunable.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from brain_graph.subgraph import GraphEdge, Subgraph


@dataclass(frozen=True)
class MotifMatch:
    motif: str
    members: tuple[tuple[str, str], ...]  # (kind, id) per member
    confidence: float
    evidence: dict[str, str | int | float]


# ---------------------------------------------------------------------------
# voice_sms_momo_24h
# ---------------------------------------------------------------------------


def detect_voice_sms_momo_24h(
    sg: Subgraph,
    *,
    sms_after_call_max_ms: int = 60 * 60 * 1000,       # 1h
    momo_after_sms_max_ms: int = 24 * 60 * 60 * 1000,  # 24h
) -> list[MotifMatch]:
    """The fingerprint pattern. Returns one MotifMatch per (caller, callee,
    wallet) triple that satisfies the temporal chain."""
    calls = _by_endpoints(sg.edges_of("CALLED"))
    smses = _by_endpoints(sg.edges_of("SMSED"))
    owns = _outgoing_dst_ids(sg.edges_of("OWNS"), src_kind="Number", dst_kind="Wallet")
    sends = _outgoing_edges(sg.edges_of("SENT"), src_kind="Wallet", dst_kind="Wallet")

    matches: list[MotifMatch] = []
    for (caller, callee), call_edges in calls.items():
        sms_edges = smses.get((caller, callee), [])
        if not sms_edges:
            continue
        wallets = owns.get(callee, [])
        if not wallets:
            continue
        for c in call_edges:
            sms_match = next(
                (s for s in sms_edges if 0 < (s.ts_ms - c.ts_ms) <= sms_after_call_max_ms),
                None,
            )
            if sms_match is None:
                continue
            for w in wallets:
                send_edges = sends.get(w, [])
                send_match = next(
                    (
                        e
                        for e in send_edges
                        if 0 < (e.ts_ms - sms_match.ts_ms) <= momo_after_sms_max_ms
                    ),
                    None,
                )
                if send_match is None:
                    continue
                matches.append(
                    MotifMatch(
                        motif="voice_sms_momo_24h",
                        members=(
                            ("Number", caller),
                            ("Number", callee),
                            ("Wallet", w),
                            ("Wallet", send_match.dst_id),
                        ),
                        confidence=0.9,
                        evidence={
                            "call_ts_ms": c.ts_ms,
                            "sms_ts_ms": sms_match.ts_ms,
                            "send_ts_ms": send_match.ts_ms,
                            "amount_minor": int(send_match.properties.get("amount", 0) or 0),
                            "lag_call_to_sms_s": (sms_match.ts_ms - c.ts_ms) // 1000,
                            "lag_sms_to_send_s": (send_match.ts_ms - sms_match.ts_ms) // 1000,
                        },
                    )
                )
    return matches


# ---------------------------------------------------------------------------
# mule_chain
# ---------------------------------------------------------------------------


def detect_mule_chains(
    sg: Subgraph,
    *,
    min_length: int = 3,
    max_length: int = 6,
    chain_window_ms: int = 24 * 60 * 60 * 1000,
) -> list[MotifMatch]:
    """Linear wallet→wallet→wallet chains in time order. Each hop must
    happen after the previous within `chain_window_ms`."""
    by_src: dict[str, list[GraphEdge]] = defaultdict(list)
    for e in sg.edges_of("SENT"):
        by_src[e.src_id].append(e)
    for lst in by_src.values():
        lst.sort(key=lambda e: e.ts_ms)

    matches: list[MotifMatch] = []
    for start in by_src:
        for chain in _walk_chains(start, by_src, min_length, max_length, chain_window_ms):
            members = tuple(("Wallet", w) for w in chain)
            total = sum(
                int(e.properties.get("amount", 0) or 0)
                for e in _edges_for(chain, by_src)
            )
            matches.append(
                MotifMatch(
                    motif="mule_chain",
                    members=members,
                    confidence=min(0.95, 0.6 + 0.1 * (len(chain) - min_length)),
                    evidence={
                        "length": len(chain),
                        "total_amount_minor": total,
                    },
                )
            )
    return matches


def _walk_chains(
    start: str,
    by_src: dict[str, list[GraphEdge]],
    min_length: int,
    max_length: int,
    window_ms: int,
) -> list[list[str]]:
    """DFS over time-ordered hops. Visited set prevents cycles."""
    out: list[list[str]] = []

    def go(node: str, path: list[str], last_ts_ms: int) -> None:
        if len(path) >= min_length:
            out.append(list(path))
        if len(path) >= max_length:
            return
        for e in by_src.get(node, []):
            if e.dst_id in path:
                continue
            if last_ts_ms and e.ts_ms - last_ts_ms > window_ms:
                continue
            if last_ts_ms and e.ts_ms < last_ts_ms:
                continue
            path.append(e.dst_id)
            go(e.dst_id, path, e.ts_ms)
            path.pop()

    go(start, [start], 0)
    # Filter to maximal chains: drop any chain that's a strict prefix of
    # another already kept.
    out.sort(key=len, reverse=True)
    keep: list[list[str]] = []
    for chain in out:
        sig = tuple(chain)
        if any(sig[: len(k)] == tuple(k) for k in keep):
            continue
        keep.append(chain)
    return keep


def _edges_for(chain: list[str], by_src: dict[str, list[GraphEdge]]) -> list[GraphEdge]:
    edges: list[GraphEdge] = []
    for a, b in zip(chain, chain[1:]):
        match = next((e for e in by_src.get(a, []) if e.dst_id == b), None)
        if match is not None:
            edges.append(match)
    return edges


# ---------------------------------------------------------------------------
# sim_carousel
# ---------------------------------------------------------------------------


def detect_sim_carousels(
    sg: Subgraph,
    *,
    min_numbers_per_device: int = 3,
) -> list[MotifMatch]:
    """A device shared by 3+ distinct numbers — the SIM-swap signature."""
    by_device: dict[str, set[str]] = defaultdict(set)
    for e in sg.edges_of("USED"):
        if e.src_kind == "Number" and e.dst_kind == "Device":
            by_device[e.dst_id].add(e.src_id)

    matches: list[MotifMatch] = []
    for device_id, numbers in by_device.items():
        if len(numbers) < min_numbers_per_device:
            continue
        members: tuple[tuple[str, str], ...] = (
            ("Device", device_id),
            *((("Number", n) for n in sorted(numbers))),
        )
        matches.append(
            MotifMatch(
                motif="sim_carousel",
                members=members,
                confidence=min(0.95, 0.55 + 0.1 * (len(numbers) - min_numbers_per_device)),
                evidence={
                    "numbers_per_device": len(numbers),
                    "device_id": device_id,
                },
            )
        )
    return matches


# ---------------------------------------------------------------------------
# bust_out
# ---------------------------------------------------------------------------


def detect_bust_outs(
    sg: Subgraph,
    *,
    dormancy_window_ms: int = 30 * 24 * 60 * 60 * 1000,   # 30d
    burst_window_ms: int = 24 * 60 * 60 * 1000,            # 24h
    dormancy_max_txns: int = 3,
    burst_min_txns: int = 5,
    burst_min_total_minor: int = 100_000,                  # 1,000.00 in pesewas
) -> list[MotifMatch]:
    """A wallet with <= `dormancy_max_txns` over `dormancy_window_ms`
    that suddenly produces a `burst_min_txns`-event cash-out cluster
    within `burst_window_ms`."""
    by_src: dict[str, list[GraphEdge]] = defaultdict(list)
    for e in sg.edges_of("SENT") + sg.edges_of("CASHED_OUT_TO"):
        if e.src_kind == "Wallet":
            by_src[e.src_id].append(e)
    for lst in by_src.values():
        lst.sort(key=lambda e: e.ts_ms)

    matches: list[MotifMatch] = []
    for wallet, edges in by_src.items():
        if len(edges) < burst_min_txns:
            continue
        latest_ts = edges[-1].ts_ms
        burst = [e for e in edges if e.ts_ms >= latest_ts - burst_window_ms]
        before = [e for e in edges if e.ts_ms < latest_ts - burst_window_ms]
        dormancy_floor = latest_ts - burst_window_ms - dormancy_window_ms
        dormant_set = [e for e in before if e.ts_ms >= dormancy_floor]
        if len(dormant_set) > dormancy_max_txns:
            continue
        if len(burst) < burst_min_txns:
            continue
        burst_total = sum(int(e.properties.get("amount", 0) or 0) for e in burst)
        if burst_total < burst_min_total_minor:
            continue
        matches.append(
            MotifMatch(
                motif="bust_out",
                members=(("Wallet", wallet),),
                confidence=0.88,
                evidence={
                    "dormancy_txns": len(dormant_set),
                    "burst_txns": len(burst),
                    "burst_total_minor": burst_total,
                    "burst_window_ms": burst_window_ms,
                },
            )
        )
    return matches


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _by_endpoints(edges: list[GraphEdge]) -> dict[tuple[str, str], list[GraphEdge]]:
    out: dict[tuple[str, str], list[GraphEdge]] = defaultdict(list)
    for e in edges:
        out[(e.src_id, e.dst_id)].append(e)
    for lst in out.values():
        lst.sort(key=lambda e: e.ts_ms)
    return out


def _outgoing_dst_ids(
    edges: list[GraphEdge], *, src_kind: str, dst_kind: str
) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        if e.src_kind == src_kind and e.dst_kind == dst_kind:
            out[e.src_id].append(e.dst_id)
    return out


def _outgoing_edges(
    edges: list[GraphEdge], *, src_kind: str, dst_kind: str
) -> dict[str, list[GraphEdge]]:
    out: dict[str, list[GraphEdge]] = defaultdict(list)
    for e in edges:
        if e.src_kind == src_kind and e.dst_kind == dst_kind:
            out[e.src_id].append(e)
    for lst in out.values():
        lst.sort(key=lambda e: e.ts_ms)
    return out
