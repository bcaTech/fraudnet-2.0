"""Cross-opco graph federation protocol.

Phase 4 of the FraudNet build (CLAUDE.md §13). Each MTN opco runs its own
FraudNet instance. The federation protocol lets opcos exchange fraud
intelligence without ever moving raw PII.

The hard rule: **PII stays local**. Only hashed identifiers, anonymized
device fingerprints, and aggregate risk scores cross opco boundaries.

Two halves:

  - `FederationClient` — outbound. Connects to a remote opco's federation
    endpoint, queries for matching hashed identifiers, and merges the
    response into the local subgraph view.

  - `FederationServer` — inbound. A FastAPI router exposing the local
    graph as a hashed-identifier read surface. Auth is HMAC-SHA256 with
    a per-peer shared secret (Phase 4 simplification — RotatingMTLS
    lands when the cross-opco PKI is in place).

  - `hash_identifier()` — the canonical hashing used on both sides. The
    salt is global (`FRAUDNET_FEDERATION_SALT`) so that hashes match
    across opcos. The salt is rotated on a fixed schedule by Group IT;
    rotations carry a 7-day overlap window during which both old and new
    hashes are accepted.

The wire format is documented in `docs/architecture/federation-protocol.md`.
"""

from fraudnet.federation.adapters import InMemoryFederationAdapter
from fraudnet.federation.client import FederationClient, FederationPeer, FederationError
from fraudnet.federation.hashing import (
    DEFAULT_SALT,
    anonymize_device_fingerprint,
    hash_identifier,
    hash_identifier_with_salt,
)
from fraudnet.federation.merge import merged_subgraph_view, RemoteSubgraph
from fraudnet.federation.server import (
    FederationServerSettings,
    LocalGraphAdapter,
    create_router,
    verify_signature,
)

__all__ = [
    "DEFAULT_SALT",
    "FederationClient",
    "FederationError",
    "FederationPeer",
    "FederationServerSettings",
    "InMemoryFederationAdapter",
    "LocalGraphAdapter",
    "RemoteSubgraph",
    "anonymize_device_fingerprint",
    "create_router",
    "hash_identifier",
    "hash_identifier_with_salt",
    "merged_subgraph_view",
    "verify_signature",
]
