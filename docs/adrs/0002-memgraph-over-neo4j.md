# ADR 0002 — Memgraph as the production graph database

- **Status:** Accepted
- **Date:** 2026-01-22
- **Deciders:** Platform engineering, data science, fraud engineering

## Context

The graph is the integrating substrate for FraudNet 2.0 — every signal becomes a node or edge, and the moat is detecting cross-domain motifs (voice → SMS → MoMo cash-out within 24 h). Workload characteristics:

- High-frequency mutation: every voice call, SMS, and MoMo transaction creates or updates edges. Order of magnitude 10k mutations/sec sustained.
- Streaming graph queries: motif detection runs continuously over the active subgraph.
- Cypher familiarity required — the team already uses Cypher tooling.
- In-memory acceptable: durability is provided by Kafka replay + lakehouse.

## Decision

**Memgraph** in production. Not Neo4j.

Rationale:

- Memgraph is designed for in-memory streaming graphs with continuous mutation; Neo4j's storage engine is optimised for read-heavy disk-resident workloads.
- Real-time query performance at our mutation rates is materially better in Memgraph at the volumes we benchmarked.
- Cypher compatibility means tooling, query patterns, and engineer mental models translate.
- Native streaming integration with Kafka.

## Consequences

**Positive**

- Sub-100 ms motif detection latency budget is achievable.
- Continuous graph mutation does not regress query performance.

**Negative**

- In-memory means restart loses transient state. Recovery is via Kafka replay, not Memgraph durability.
- Operational tooling and community size are smaller than Neo4j's. We pay for Memgraph Enterprise for replication and support.

**Mitigations**

- Snapshots every 6 hours; events between snapshots replayed from Kafka on recovery.
- Replication to a hot standby (Memgraph Enterprise).
- All graph access goes through `packages/graph-client` so a future swap to Neo4j or another engine is a single integration point.
