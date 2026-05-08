# Data contracts

Inter-service contracts. Authoritative source for every wire format crossing a service boundary.

| Layer | Format | Source of truth |
|---|---|---|
| Kafka topic payloads | Avro (Confluent Schema Registry) | `packages/schemas/avro/` |
| HTTP REST | OpenAPI 3.1 | Each service's `openapi.yaml`, generated from FastAPI |
| Internal RPC | gRPC + Protobuf | `packages/schemas/proto/` |
| Database schemas | SQL migrations | `services/*/migrations/` |
| Graph schema | Cypher DDL | `packages/graph-client/schema.cypher` |

## Compatibility rules

- **Avro:** add fields with defaults only. Never reorder. Never repurpose. Required field additions need a topic version bump (`*.v2`) and a dual-publish migration.
- **OpenAPI:** same backward-compatibility rules; breaking changes go to `/v2/`.
- **Protobuf:** field numbers are immutable once shipped.

Run `make test-contracts` on every PR.
