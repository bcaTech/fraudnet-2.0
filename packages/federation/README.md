# fraudnet-federation

Cross-opco graph federation protocol. Phase 4 of the FraudNet build
(CLAUDE.md §13).

## What it is

Each MTN opco runs its own FraudNet instance. The federation protocol lets
opcos exchange fraud intelligence without ever moving raw PII. Three
operations:

1. **Lookup** — "Does your opco have intelligence on these hashed
   identifiers?" Bulk membership test.
2. **Subgraph query** — "Pull the k-hop neighbourhood around these hashed
   nodes from your graph." Used to detect rings whose membership crosses
   opcos.
3. **Publish** — "Here's a flag we've raised; please add it to your view."
   Used by the block-request flow in `api-enterprise` to escalate cross-
   network blocks.

## What crosses the boundary

| Wire field            | Form                                   |
| --------------------- | -------------------------------------- |
| MSISDN, wallet ID, account number | salted SHA-256 hex (64 chars) |
| IMEI                  | salted SHA-256, truncated to 16 chars  |
| Risk scores           | float in `[0.0, 1.0]`                  |
| Edge timestamps       | epoch ms                               |
| Edge / node kinds     | enum strings (`CALLED`, `Number`, ...) |

What does **not** cross: any plaintext identifier, raw call/SMS bodies,
geographic coordinates beyond the city level, anything in the
`packages/schemas` PII denylist.

## Authentication

HMAC-SHA256 over `{ts}|{method}|{path}|{body_sha256}` with a per-peer-pair
shared secret. 5-minute clock-skew tolerance, replay-protected via
timestamp window.

mTLS + SPIFFE workload identity replaces this in Phase 4.5 once Group IT's
cross-opco PKI is rolled out. The interface is identical from the caller's
perspective.

## Salt rotation

The hashing salt is global (`FRAUDNET_FEDERATION_SALT`). Rotation is
coordinated by Group IT and carries a 7-day overlap window during which
both old and new hashes are accepted.

## Wire format

See `protocol.py` for the Pydantic models. URL versioning is path-based
(`/federation/v1/...`); breaking changes get a new major version.

## Use

Server side (FastAPI router):

```python
from fraudnet.federation import (
    FederationServerSettings,
    InMemoryFederationAdapter,
    create_router,
)

adapter = MemgraphFederationAdapter(graph_client)  # or InMemoryFederationAdapter()
settings = FederationServerSettings(
    server_id="opco-ghana",
    peer_secrets={"opco-uganda": os.environ["FED_SECRET_UGANDA"]},
)
app.include_router(create_router(settings=settings, adapter=adapter))
```

Client side:

```python
from fraudnet.federation import FederationClient, FederationPeer

client = FederationClient({
    "opco-uganda": FederationPeer(
        name="opco-uganda",
        base_url="https://opco-uganda.fraudnet.internal",
        shared_secret=os.environ["FED_SECRET_UGANDA"],
    ),
})

resp = await client.lookup_flags(
    peer="opco-uganda",
    identifier_hashes=[hash_identifier("+233200000001", kind="msisdn")],
)
```

## Tests

```bash
pytest packages/federation -v
```
