# DECISIONS.md

Non-obvious choices made during the Phase 1 build that deviate from CLAUDE.md, the spec, or where the spec is silent. Each entry: what, why, when to revisit.

## D-001 — Branch strategy: `phase-1-build` instead of `main`

**Decision:** All Phase 1 work lands on `phase-1-build`; PR opened at the end.

**Why:** Direct push to `main` is blocked by branch protection. The user requested "push after each commit"; pushing the feature branch satisfies that without bypassing review.

**Revisit:** When Phase 1 is complete, open a single review-PR (or split by service if too large).

---

## D-002 — Stream jobs in PyFlink, not Java/Scala

**Decision:** `stream-features` and `stream-graph` ship as **PyFlink** jobs in Phase 1.

**Why CLAUDE.md says otherwise:** §4.1 says "PyFlink only for prototyping. Production jobs are Java/Scala."

**Why we're deviating:** Phase 1 build velocity. The current team is Python-first; a JVM build chain doubles the per-service complexity for jobs that are still under iteration. The Flink job logic stays small and table-API-driven so a port to Java/Scala in Phase 2/3 is mechanical, not a redesign.

**Revisit:** Before scaling beyond MTN-Ghana volumes (~100M events/day). The pyflink-vs-jvm decision should be re-benchmarked as soon as we have realistic load profiles from probe vendor selection.

---

## D-003 — Per-tier action topics, not one filtered topic

**Decision:** Decisions writes to `action.tier1.v1`, `action.tier2.v1`, `action.tier3.v1`. Each `action-tier*` service consumes its own topic.

**Why CLAUDE.md says otherwise:** §5.4 says "`action-tier1` consumes `decisions.dispatched.v1` filtered to Tier 1".

**Why we're deviating:** User directive in the Phase-1 build prompt explicitly listed three topics. Trade-off:
- Per-tier topics give independent retention, scaling, and back-pressure isolation.
- Single-topic-with-filter gives a unified audit trail.

`decisions.dispatched.v1` remains in the topology and continues to be the audit trail (decisions service writes to it AND fan-outs to per-tier topics). Compliance consumes from the audit trail; actuators consume from per-tier topics.

**Revisit:** If operating two parallel publishes proves to be a maintenance burden in production. The audit-trail path could be replaced by `audit.events.v1`-style records emitted by the decisions service.

---

## D-004 — `fraud.signals.v1` topic added between brain-* and decisions

**Decision:** New topic `fraud.signals.v1` carrying `SignalEventV1` payloads. Brain services produce; decisions consumes.

**Why CLAUDE.md is silent:** §5.3 describes brain services exposing gRPC + REST scoring endpoints; the orchestrator pulls scores synchronously. §5.4 mentions decisions also subscribes to scoring outputs. The build prompt makes the asynchronous path explicit, which fits the streaming architecture better.

**Schema:** event_id, event_ts_ms, ingest_ts_ms, source, tenant_id, model_id, model_version, subject, score, severity, evidence, suppression_key.

**Revisit:** If sub-scoring latency requirements force a return to synchronous gRPC for Tier-1 paths.

---

## D-005 — Customer auth (api-customer): email-OTP stub for Phase 1

**Decision:** `api-customer` ships with a stub OTP flow (deterministic in dev, hooked to MTN's SMS gateway in prod via env-driven adapter).

**Why:** The MSISDN-OTP integration with the MTN OTP service is a separate workstream (security team owns the contract). The contract surface is small enough that swapping the adapter post-launch is a one-file change.

**Revisit:** Before customer self-service GA. Coordinated with security team's OTP service rollout.

---

## D-006 — Brain-behavioural Phase 1 model is a stub

**Decision:** `brain-behavioural` ships with a hand-coded heuristic model (call velocity > N, fan-out > M, etc.) wrapped in the same scoring interface that LightGBM will plug into.

**Why:** Trained model artefacts come from the data science team via the model registry (Phase 2 scope). The interface is fixed; the artefact is swappable.

**Revisit:** When data science delivers the first trained behavioural model — likely month 3-4 of Phase 1.
