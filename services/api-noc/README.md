# api-noc

NOC investigator workbench API. JWT auth, RBAC via `@require_role`, Postgres + Memgraph reads, takedown workflow as a guarded state machine. Per CLAUDE.md §5.5.

## Endpoints

| Method | Path | RBAC | Purpose |
|---|---|---|---|
| `GET`  | `/alerts` | NOC_VIEWER+ | List alerts (status / severity filters, paged) |
| `GET`  | `/alerts/{id}` | NOC_VIEWER+ | Alert detail |
| `POST` | `/alerts/{id}/claim` | FRAUD_ANALYST+ | Claim (race-safe — second claimer gets 409) |
| `POST` | `/alerts/{id}/close` | FRAUD_ANALYST+ | Close (resolved or false-positive) |
| `GET`  | `/rings` | NOC_VIEWER+ | List rings |
| `GET`  | `/rings/{id}` | NOC_VIEWER+ | Ring + member detail (Postgres + ring_members) |
| `GET`  | `/rings/{id}/graph` | FRAUD_ANALYST+ | Memgraph subgraph (depth ≤ 4, max 1000 nodes) |
| `POST` | `/takedowns` | FRAUD_LEAD+ | Create draft takedown |
| `POST` | `/takedowns/{id}/transition` | FRAUD_LEAD+ | State-machine transition |

## Takedown state machine

```
drafted → approved → filed → acknowledged → executed → closed
   └─────────┴────────┴─────────────┴──────────────────┘
                  any → closed
```

Invalid transitions return HTTP 409.

## Auth

Keycloak JWT bearer. Middleware extracts the token, validates against the JWKS, and binds the resulting `Principal` to `request.state.principal`. RBAC decorators read it.

Tests bypass auth via `create_app(test_principal=...)`.

## Audit

Every read of PII data and every write goes through `with_purpose(FRAUD_PREVENTION)` and emits a `record(...)` audit event with actor, resource, and metadata.

## Migrations

`migrations/0001_initial.sql` defines `users`, `rings`, `ring_members`, `alerts`, `takedowns` per CLAUDE.md §6.1.

## Endpoints (operational)

| Path | Purpose |
|---|---|
| `GET /health/{live,ready}` | k8s probes (ready checks DB) |
| `GET /metrics` | Prometheus scrape |

## Runbook

[`docs/runbooks/api-noc.md`](../../docs/runbooks/api-noc.md)
