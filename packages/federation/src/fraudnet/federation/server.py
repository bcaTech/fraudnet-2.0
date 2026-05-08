"""Federation server — the inbound side of the protocol.

A FastAPI router that exposes a hashed read view of the local graph. Routes:

  - POST /federation/v1/flags/lookup   — bulk hash → flag membership test
  - POST /federation/v1/flags/publish  — peer pushes a flag to us
  - POST /federation/v1/subgraph/query — peer asks for hashed k-hop neighbourhood

Every request is HMAC-signed (see `auth.py`). Unsigned or stale requests
return 401 without dispatching the route handler.

The router is intentionally agnostic of the underlying graph store — it
delegates to a `LocalGraphAdapter` Protocol. The default adapter wraps
`fraudnet.graph.GraphClient`; tests pass an in-memory fake.

PII enforcement: the adapter MUST hash all returned identifiers using the
configured salt before they leave this process. The router does not hash
on the way out — that responsibility is on the adapter so it cannot be
bypassed by a bug in the router.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from fastapi import APIRouter, Header, HTTPException, Request

from fraudnet.obs import counter, get_logger

from fraudnet.federation.auth import verify_signature
from fraudnet.federation.protocol import (
    FederationFlag,
    FederationFlagPublishRequest,
    FederationFlagPublishResponse,
    FederationLookupRequest,
    FederationLookupResponse,
    FederationSubgraphRequest,
    FederationSubgraphResponse,
    RemoteEdge,
    RemoteNode,
)

_log = get_logger("fraudnet.federation.server")

_INBOUND = counter(
    "federation_server_requests_total",
    "Federation server inbound requests.",
    labelnames=("peer", "op", "outcome"),
)


# ---------------------------------------------------------------------------
# Adapter contract — the router does not know about Memgraph or Postgres.
# ---------------------------------------------------------------------------


class LocalGraphAdapter(Protocol):
    """The local opco's view exposed to peers, hashed.

    The adapter implementations in production (Memgraph) hash all identifiers
    inside the query — they never return a plaintext MSISDN to the router.
    """

    async def lookup_flags(
        self, *, identifier_hashes: list[str]
    ) -> list[FederationFlag]: ...

    async def get_subgraph(
        self,
        *,
        seed_hashes: list[str],
        depth: int,
        max_nodes: int,
    ) -> tuple[list[RemoteNode], list[RemoteEdge], bool]: ...

    async def accept_flag(self, *, flag: FederationFlag, peer_name: str) -> bool: ...


# ---------------------------------------------------------------------------
# Settings + signature middleware
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FederationServerSettings:
    server_id: str
    salt_version: str = "v1"
    # Map of peer name → shared secret. The peer name comes from the
    # `X-Federation-Peer` header.
    peer_secrets: dict[str, str] = ()  # type: ignore[assignment]


def _resolve_peer_secret(
    settings: FederationServerSettings, peer_name: str | None
) -> str | None:
    if not peer_name:
        return None
    secrets = settings.peer_secrets or {}
    if isinstance(secrets, dict):
        return secrets.get(peer_name)
    return None


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_router(
    *,
    settings: FederationServerSettings,
    adapter: LocalGraphAdapter,
) -> APIRouter:
    router = APIRouter(prefix="/federation/v1")

    async def _verify(
        request: Request,
        body: bytes,
        peer_name: str | None,
        timestamp: str | None,
        signature: str | None,
    ) -> str:
        secret = _resolve_peer_secret(settings, peer_name)
        if secret is None:
            _INBOUND.labels(
                peer=peer_name or "unknown", op=request.url.path, outcome="auth_no_peer"
            ).inc()
            raise HTTPException(status_code=401, detail="unknown peer")
        ok = verify_signature(
            secret=secret,
            method=request.method,
            path=request.url.path,
            body=body,
            timestamp=timestamp,
            signature=signature,
        )
        if not ok:
            _INBOUND.labels(
                peer=peer_name, op=request.url.path, outcome="auth_invalid"
            ).inc()
            raise HTTPException(status_code=401, detail="invalid signature")
        return peer_name

    @router.post("/flags/lookup", response_model=FederationLookupResponse)
    async def lookup(
        request: Request,
        x_federation_peer: str | None = Header(default=None),
        x_federation_timestamp: str | None = Header(default=None),
        x_federation_signature: str | None = Header(default=None),
    ) -> FederationLookupResponse:
        body = await request.body()
        peer = await _verify(
            request, body, x_federation_peer, x_federation_timestamp, x_federation_signature
        )
        req = FederationLookupRequest.model_validate_json(body)
        matched = await adapter.lookup_flags(identifier_hashes=req.identifier_hashes)
        _INBOUND.labels(peer=peer, op="lookup", outcome="ok").inc()
        return FederationLookupResponse(
            matched=matched,
            salt_version=settings.salt_version,
            server_id=settings.server_id,
        )

    @router.post("/subgraph/query", response_model=FederationSubgraphResponse)
    async def subgraph(
        request: Request,
        x_federation_peer: str | None = Header(default=None),
        x_federation_timestamp: str | None = Header(default=None),
        x_federation_signature: str | None = Header(default=None),
    ) -> FederationSubgraphResponse:
        body = await request.body()
        peer = await _verify(
            request, body, x_federation_peer, x_federation_timestamp, x_federation_signature
        )
        req = FederationSubgraphRequest.model_validate_json(body)
        nodes, edges, truncated = await adapter.get_subgraph(
            seed_hashes=req.seed_hashes,
            depth=req.depth,
            max_nodes=req.max_nodes,
        )
        _INBOUND.labels(peer=peer, op="subgraph", outcome="ok").inc()
        return FederationSubgraphResponse(
            nodes=nodes,
            edges=edges,
            truncated=truncated,
            server_id=settings.server_id,
            salt_version=settings.salt_version,
        )

    @router.post("/flags/publish", response_model=FederationFlagPublishResponse)
    async def publish(
        request: Request,
        x_federation_peer: str | None = Header(default=None),
        x_federation_timestamp: str | None = Header(default=None),
        x_federation_signature: str | None = Header(default=None),
    ) -> FederationFlagPublishResponse:
        body = await request.body()
        peer = await _verify(
            request, body, x_federation_peer, x_federation_timestamp, x_federation_signature
        )
        req = FederationFlagPublishRequest.model_validate_json(body)
        accepted = await adapter.accept_flag(flag=req.flag, peer_name=peer)
        _INBOUND.labels(
            peer=peer, op="publish", outcome="accepted" if accepted else "rejected"
        ).inc()
        return FederationFlagPublishResponse(accepted=accepted)

    return router


# A thin re-export so callers don't need to reach into auth.py to verify
# inbound signatures (e.g. for custom non-FastAPI integrations).
__all_ = ["verify_signature"]


# Convenience re-export
def _placeholder_async(_callback: Callable[..., Awaitable[object]]) -> None:
    """Type-checker hint placeholder — not used at runtime."""
    return None
