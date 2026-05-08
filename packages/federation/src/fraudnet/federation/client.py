"""Federation client.

Connects to a remote opco's federation endpoint and exchanges hashed-
identifier intelligence. Three operations:

  - `lookup_flags(hashes)` — bulk membership test against the peer's flag
    table. Returns matched flags only.
  - `query_subgraph(seeds, depth, max_nodes)` — pull a hashed neighbourhood
    around a set of seed identifiers.
  - `publish_flag(flag)` — push a flag to the peer's accept queue.

The client is async (httpx). One client serves multiple peers; the
`peer` argument resolves to a `FederationPeer` (URL + secret) at call time.

PII never leaves the local opco via this client; callers must hash before
calling. The client *will not hash for you* — that asymmetry forces the
caller to be deliberate about what crosses the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from fraudnet.obs import counter, get_logger, histogram
from fraudnet.federation.auth import sign_request
from fraudnet.federation.protocol import (
    FederationFlag,
    FederationFlagPublishRequest,
    FederationFlagPublishResponse,
    FederationLookupRequest,
    FederationLookupResponse,
    FederationSubgraphRequest,
    FederationSubgraphResponse,
)

_log = get_logger("fraudnet.federation.client")

_REQUESTS = counter(
    "federation_client_requests_total",
    "Federation client outbound requests.",
    labelnames=("peer", "op", "outcome"),
)
_DURATION = histogram(
    "federation_client_request_seconds",
    "Federation client request duration.",
    labelnames=("peer", "op"),
)


class FederationError(Exception):
    """Federation request failed (network, auth, or server-side)."""


@dataclass(frozen=True)
class FederationPeer:
    """A remote opco's federation endpoint.

    `name` matches the local tenant slug used in audit logs ("mtn-nigeria",
    "mtn-uganda", etc.).
    """

    name: str
    base_url: str
    shared_secret: str
    timeout_s: float = 5.0


class FederationClient:
    def __init__(self, peers: dict[str, FederationPeer]) -> None:
        self._peers = peers
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "FederationClient":
        self._http = httpx.AsyncClient()
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    @property
    def peers(self) -> tuple[str, ...]:
        return tuple(self._peers)

    async def lookup_flags(
        self, *, peer: str, identifier_hashes: list[str]
    ) -> FederationLookupResponse:
        """Ask the peer if any of the listed hashes are flagged.

        Empty list → empty response (short-circuited locally; no network).
        """
        if not identifier_hashes:
            return FederationLookupResponse(matched=[], server_id=peer)
        body = FederationLookupRequest(identifier_hashes=identifier_hashes)
        raw = body.model_dump_json().encode()
        return await self._post(
            peer=peer,
            path="/federation/v1/flags/lookup",
            body=raw,
            response_model=FederationLookupResponse,
            op="lookup_flags",
        )

    async def query_subgraph(
        self,
        *,
        peer: str,
        seed_hashes: list[str],
        depth: int = 2,
        max_nodes: int = 100,
    ) -> FederationSubgraphResponse:
        """Pull the peer's k-hop neighbourhood around the seed hashes.

        The returned subgraph contains hashed identifiers only. Caller is
        responsible for merging into the local view (`merge.py`).
        """
        body = FederationSubgraphRequest(
            seed_hashes=seed_hashes, depth=depth, max_nodes=max_nodes
        )
        raw = body.model_dump_json().encode()
        return await self._post(
            peer=peer,
            path="/federation/v1/subgraph/query",
            body=raw,
            response_model=FederationSubgraphResponse,
            op="query_subgraph",
        )

    async def publish_flag(
        self,
        *,
        peer: str,
        identifier_hash: str,
        identifier_kind: str,
        indicator_kind: str,
        confidence: float,
        first_seen_ms: int | None = None,
        last_seen_ms: int | None = None,
        evidence: dict[str, object] | None = None,
    ) -> bool:
        """Push a flag to the peer. Returns True iff accepted.

        The peer may reject the flag (rate limit, malformed, peer paused) —
        rejection is *not* an exception. A `FederationError` indicates a
        transport-level failure.
        """
        from time import time

        now_ms = int(time() * 1000)
        flag = FederationFlag(
            identifier_hash=identifier_hash,
            identifier_kind=identifier_kind,
            indicator_kind=indicator_kind,
            confidence=confidence,
            first_seen_ms=first_seen_ms or now_ms,
            last_seen_ms=last_seen_ms or now_ms,
            evidence=evidence or {},
        )
        body = FederationFlagPublishRequest(flag=flag)
        raw = body.model_dump_json().encode()
        resp = await self._post(
            peer=peer,
            path="/federation/v1/flags/publish",
            body=raw,
            response_model=FederationFlagPublishResponse,
            op="publish_flag",
        )
        return resp.accepted

    async def _post(
        self,
        *,
        peer: str,
        path: str,
        body: bytes,
        response_model: type,
        op: str,
    ):  # noqa: ANN202
        peer_cfg = self._peers.get(peer)
        if peer_cfg is None:
            raise FederationError(f"unknown federation peer: {peer}")
        if self._http is None:
            self._http = httpx.AsyncClient()
        url = peer_cfg.base_url.rstrip("/") + path
        headers = sign_request(
            secret=peer_cfg.shared_secret,
            method="POST",
            path=path,
            body=body,
        )
        headers["Content-Type"] = "application/json"
        try:
            with _DURATION.labels(peer=peer, op=op).time():
                resp = await self._http.post(
                    url, content=body, headers=headers, timeout=peer_cfg.timeout_s
                )
        except httpx.HTTPError as exc:
            _REQUESTS.labels(peer=peer, op=op, outcome="transport_error").inc()
            raise FederationError(f"federation transport error: {exc}") from exc

        if resp.status_code >= 400:
            _REQUESTS.labels(peer=peer, op=op, outcome=f"http_{resp.status_code}").inc()
            raise FederationError(
                f"federation peer {peer} returned {resp.status_code}: {resp.text[:200]}"
            )
        _REQUESTS.labels(peer=peer, op=op, outcome="ok").inc()
        return response_model.model_validate_json(resp.content)


def parse_peers(spec: str, *, shared_secret: str) -> dict[str, FederationPeer]:
    """Parse a comma-separated peer spec.

    Format: ``name1=https://opco1.example,name2=https://opco2.example``.
    Empty spec → no peers (federation effectively disabled).
    """
    out: dict[str, FederationPeer] = {}
    if not spec:
        return out
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            _log.warning("federation.peer.malformed", spec=chunk)
            continue
        name, url = chunk.split("=", 1)
        out[name.strip()] = FederationPeer(
            name=name.strip(),
            base_url=url.strip(),
            shared_secret=shared_secret,
        )
    return out
