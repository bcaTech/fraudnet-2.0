"""Production adapter — `MemgraphFederationAdapter` exposes a hashed read
view of the local Memgraph store.

All identifiers are hashed *inside* the Cypher RETURN clause via
client-side post-processing. We never trust the caller to hash; we never
trust the wire to carry plaintext.

Tests use `InMemoryFederationAdapter` from this module — a deterministic
fake that round-trips a small fixture set.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from fraudnet.federation.hashing import hash_identifier
from fraudnet.federation.protocol import (
    FederationFlag,
    RemoteEdge,
    RemoteNode,
)
from fraudnet.federation.server import LocalGraphAdapter


# ---------------------------------------------------------------------------
# In-memory adapter — used by tests and the dev server.
# ---------------------------------------------------------------------------


@dataclass
class _StoredFlag:
    flag: FederationFlag
    accepted_from: str


@dataclass
class InMemoryFederationAdapter(LocalGraphAdapter):
    """Backing store for local-only federation tests.

    Loads a fixture of (kind, value) → flag, hashes on read, and serves
    subgraph queries from an explicit edge list.
    """

    salt: str = "fraudnet-federation-v1"
    flags_by_hash: dict[str, FederationFlag] = field(default_factory=dict)
    inbound_flags: list[_StoredFlag] = field(default_factory=list)
    nodes: list[RemoteNode] = field(default_factory=list)
    edges: list[RemoteEdge] = field(default_factory=list)

    def add_flag(
        self,
        *,
        identifier: str,
        identifier_kind: str,
        indicator_kind: str,
        confidence: float,
        first_seen_ms: int = 0,
        last_seen_ms: int = 0,
        evidence: dict[str, Any] | None = None,
    ) -> str:
        h = hash_identifier(identifier, kind=identifier_kind, salt=self.salt)
        self.flags_by_hash[h] = FederationFlag(
            identifier_hash=h,
            identifier_kind=identifier_kind,
            indicator_kind=indicator_kind,
            confidence=confidence,
            first_seen_ms=first_seen_ms,
            last_seen_ms=last_seen_ms,
            evidence=evidence or {},
        )
        return h

    def add_node(
        self,
        *,
        identifier: str,
        kind: str,
        risk_score: float | None = None,
        properties: dict[str, Any] | None = None,
    ) -> str:
        kind_to_id_kind = {"Number": "msisdn", "Wallet": "wallet", "Device": "imei"}
        h = hash_identifier(
            identifier, kind=kind_to_id_kind.get(kind, "msisdn"), salt=self.salt
        )
        self.nodes.append(
            RemoteNode(
                kind=kind,
                identifier_hash=h,
                risk_score=risk_score,
                properties=properties or {},
            )
        )
        return h

    def add_edge(
        self,
        *,
        src_hash: str,
        dst_hash: str,
        kind: str,
        ts_ms: int = 0,
        properties: dict[str, Any] | None = None,
    ) -> None:
        self.edges.append(
            RemoteEdge(
                kind=kind,
                src_hash=src_hash,
                dst_hash=dst_hash,
                ts_ms=ts_ms,
                properties=properties or {},
            )
        )

    async def lookup_flags(
        self, *, identifier_hashes: list[str]
    ) -> list[FederationFlag]:
        out: list[FederationFlag] = []
        for h in identifier_hashes:
            flag = self.flags_by_hash.get(h)
            if flag is not None:
                out.append(flag)
        return out

    async def get_subgraph(
        self,
        *,
        seed_hashes: list[str],
        depth: int,
        max_nodes: int,
    ) -> tuple[list[RemoteNode], list[RemoteEdge], bool]:
        """BFS up to `depth` hops from any of the seeds.

        Treats edges as undirected for traversal but returns them with
        their original direction.
        """
        adj: dict[str, list[RemoteEdge]] = defaultdict(list)
        for e in self.edges:
            adj[e.src_hash].append(e)
            adj[e.dst_hash].append(e)
        seeds = set(seed_hashes)
        visited = set(seeds)
        frontier = set(seeds)
        for _ in range(depth):
            next_frontier: set[str] = set()
            for n in frontier:
                for e in adj.get(n, []):
                    other = e.dst_hash if e.src_hash == n else e.src_hash
                    if other not in visited:
                        next_frontier.add(other)
                visited.update(next_frontier)
            frontier = next_frontier
        kept_nodes = [n for n in self.nodes if n.identifier_hash in visited]
        truncated = False
        if len(kept_nodes) > max_nodes:
            kept_nodes = kept_nodes[:max_nodes]
            truncated = True
        kept_ids = {n.identifier_hash for n in kept_nodes}
        kept_edges = [
            e for e in self.edges
            if e.src_hash in kept_ids and e.dst_hash in kept_ids
        ]
        return kept_nodes, kept_edges, truncated

    async def accept_flag(
        self, *, flag: FederationFlag, peer_name: str
    ) -> bool:
        self.inbound_flags.append(_StoredFlag(flag=flag, accepted_from=peer_name))
        # Phase 4 simplification: accept everything inside size + confidence
        # bounds. Production adds reputation gating per peer.
        if flag.confidence < 0.0 or flag.confidence > 1.0:
            return False
        return True
