# ADR 0001 — Monorepo with independently-deployable services

- **Status:** Accepted
- **Date:** 2026-01-15
- **Deciders:** Programme lead, platform engineering, fraud engineering

## Context

FraudNet 2.0 spans four reference-architecture layers (ingestion, stream processing, detection, decision/action) and three latency tiers (inline VoLTE tagging, near-real-time customer alerts, investigator workbench). Each tier has materially different infrastructure profiles and SLOs:

- Tier 1 inline scoring is co-located with the network path, runs on dedicated nodes, and has a 200 ms end-to-end budget.
- Tier 2 customer notification tolerates seconds-to-minutes latency.
- Tier 3 investigator-facing services run on commodity Kubernetes and are query-heavy.

Forcing these into a single deployable artefact would tie scaling, rollout, and resource decisions together that should be independent.

## Decision

Adopt a **monorepo with many independently-deployable services**. One git repository; one Turborepo-style build graph; per-service container images, deploy cadence, and SLOs.

- Services share types and libraries through workspace packages under `packages/`.
- Cross-service contracts are versioned: Avro for Kafka topics, OpenAPI for HTTP, gRPC protos for internal RPC.
- Each service has its own `pyproject.toml`, runbook, and rollout policy.

## Consequences

**Positive**

- Per-tier infrastructure and rollout policy without coordinating releases.
- Cross-service refactors land in single PRs with full test coverage.
- One lint/format/typecheck pipeline; one place to enforce conventions.

**Negative**

- Build cadence must scale with the workspace — Turborepo caching is mandatory.
- Engineers must read service runbooks before changing infrastructure-affecting code.

**Mitigations**

- Codeowners enforce two-engineer review with at least one on the affected service team.
- Contract tests run on every PR (`make test-contracts`).
- Per-service SLOs documented in `docs/runbooks/{service}.md`.
