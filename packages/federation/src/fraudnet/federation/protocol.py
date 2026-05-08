"""Wire-format types shared by client and server.

Pydantic models so the same types can be used by FastAPI on the server side
and the httpx client. The schemas are versioned via the URL path
(`/federation/v1/...`); breaking changes get a new major version.

PII rule: nothing in these models is plaintext PII. `identifier_hash` is
the salted SHA-256, `device_fingerprint` is the 16-char truncated hash. A
linter rule enforces that no field type is named `msisdn`, `imei`, etc.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class FederationFlag(BaseModel):
    """A single piece of fraud intelligence, hashed for cross-opco."""

    identifier_hash: str = Field(min_length=64, max_length=64)
    identifier_kind: str  # 'msisdn' | 'wallet' | 'imei' | 'url' | 'account'
    indicator_kind: str   # 'mule' | 'smishing' | 'voice_scam' | 'block_request' | ...
    confidence: float = Field(ge=0.0, le=1.0)
    first_seen_ms: int
    last_seen_ms: int
    evidence: dict[str, Any] = Field(default_factory=dict)


class FederationLookupRequest(BaseModel):
    """Ask a remote opco: 'do you have intelligence on these hashed
    identifiers?' Limit on batch size is enforced by the server."""

    identifier_hashes: list[str] = Field(min_length=1, max_length=500)


class FederationLookupResponse(BaseModel):
    """Remote opco's answer. Only matched hashes are returned; unknown
    hashes are silently dropped to avoid leaking a partial subscriber base."""

    matched: list[FederationFlag] = Field(default_factory=list)
    salt_version: str = "v1"
    server_id: str


class FederationSubgraphRequest(BaseModel):
    """Ask the remote opco: 'pull the k-hop neighbourhood of these hashed
    nodes from your graph and return it as a hashed-identifier subgraph.'
    The remote returns nodes + edges where every node identifier is a
    salted hash."""

    seed_hashes: list[str] = Field(min_length=1, max_length=50)
    depth: int = Field(default=2, ge=1, le=3)
    max_nodes: int = Field(default=100, ge=10, le=500)


class RemoteNode(BaseModel):
    kind: str             # 'Number' | 'Wallet' | 'Device' | 'Account'
    identifier_hash: str  # always the hash, never plaintext
    risk_score: float | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class RemoteEdge(BaseModel):
    kind: str  # 'CALLED' | 'SENT' | 'OWNS' | 'USED' | 'CASHED_OUT_TO' | ...
    src_hash: str
    dst_hash: str
    ts_ms: int = 0
    properties: dict[str, Any] = Field(default_factory=dict)


class FederationSubgraphResponse(BaseModel):
    nodes: list[RemoteNode] = Field(default_factory=list)
    edges: list[RemoteEdge] = Field(default_factory=list)
    truncated: bool = False
    server_id: str
    salt_version: str = "v1"


class FederationFlagPublishRequest(BaseModel):
    """Push a flag to a peer (the client publishes; the peer accepts)."""

    flag: FederationFlag


class FederationFlagPublishResponse(BaseModel):
    accepted: bool
    reason: str | None = None
