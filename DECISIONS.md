# DECISIONS.md

Non-obvious choices made during the Phase 1 build that deviate from CLAUDE.md, the spec, or where the spec is silent. Each entry: what, why, when to revisit.

## D-001 — Branch strategy: `phase-1-build` instead of `main`

**Decision:** All Phase 1 work lands on `phase-1-build`; PR opened at the end.

**Why:** Direct push to `main` is blocked by branch protection. The user requested "push after each commit"; pushing the feature branch satisfies that without bypassing review.

**Revisit:** When Phase 1 is complete, open a single review-PR (or split by service if too large).

---

## D-002 — Stream jobs ship as Python consumers in Phase 1; PyFlink wrapper ready for Phase 2

**Decision:** `stream-features` and `stream-graph` are structured as pure-Python streaming consumers in Phase 1. The transformation logic lives in a `pipeline.py` module that's table-API-friendly, with a thin `pyflink_job.py` wrapper provided for the day we promote to a Flink cluster.

**Why CLAUDE.md says otherwise:** §4.1 says "Production jobs are Java/Scala." (PyFlink is positioned as prototyping.)

**Why staged migration:**
- **Phase 1 (now):** Standalone Python consumer pod. Deployable on the existing k8s cluster, no Flink cluster ops to introduce mid-Phase-1, no JAR build chain. Uses the same `fraudnet-kafka` primitives every other service uses. Backpressure via manual commit cadence.
- **Phase 2:** Promote to PyFlink on the Flink Kubernetes Operator once we have realistic load profiles. The pipeline functions are written to be table-API-friendly so the wrapper is mechanical.
- **Phase 3+:** If/when load demands it, the table-API job ports to Java/Scala.

The cost is one extra refactor per stream service. The benefit is shipping Phase 1 without a heavy new operational dependency.

**Revisit:** Before MTN-Ghana scale tests cross ~30k events/sec sustained on a single voice partition.

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

---

## D-007 — RCS-verified messages are trusted by default

**Decision:** SMS events arriving with `rcs_verified=True` from the SMSC bypass content classification (brain-content short-circuits with no signal) and exempt the sender's MSISDN from `device.imei_churn` in brain-behavioural (via the `rcs_verified_recent` feature bin).

**Why:** RCS Business Messaging authentication is platform-grade — verified senders are cryptographically authenticated by the RCS hub; an attacker cannot trivially forge the verified-sender bit. Treating these as trusted is correct: the signal is stronger than any heuristic / ML score we can derive from the body. IMEI churn is normal for businesses that legitimately rotate SMS-routing infrastructure; flagging them generated false positives in Airtel India's deployment.

**What this affects:**
- `packages/schemas`: `SmsEventV1.rcs_verified: bool = False`. Avro schema bumped non-breakingly (default false).
- `services/ingest-sms`: adapter accepts vendor variants (`rcs_verified`, `verified_sender`, `rcs_authenticated`).
- `services/brain-content`: hard short-circuit — RCS-verified MT SMS does not run the classifier and emits no signal.
- `services/brain-behavioural`: `device.imei_churn` does not fire when `NumberFeatures.rcs_verified_recent` is true.

**Followup (Phase 2):** stream-features must populate `rcs_verified_recent` on the sender's `NumberFeatures` record from `sms.events.v1`. The feature schema and scorer change land now so the data path can light up without further code changes when stream-features ships the populator.

**Revisit:** If we ever observe spoofed RCS verification (vendor compromise, peer-network leak), revoke the trust override and route RCS-verified messages through the same scoring path as everyone else. The override is gated on the SMSC's outbound integrity, not on FraudNet code.

---

## D-008 — All subscribers are protected by default

**Decision:** Tier-2 customer-facing actions (`spam_call_warning`, `spam_sms_warning`, `otp_fraud_alert`, `url_blocked`, `fraud_alert_sms`) are auto-enabled for every MTN subscriber. The `api-customer` portal becomes an *enhancement* layer (block/unblock self-service, granular control) rather than a *gate* on protection.

**Why:** Airtel India / Africa ship their fraud-alerting service in passive mode by default. Opt-in protection is structurally weaker — the customers most exposed to fraud (older, less digital-native, low-data subscribers) are the least likely to enrol. The MTN Ghana strategy explicitly aims for ubiquitous protection; that requires it to be on without action.

**What this affects:**
- `services/decisions/policies/default.yaml`: new top-level `passive_protection` block listing the auto-enabled action set.
- `services/action-tier2`: each customer-facing actuator checks `protection_mode` (default `passive`); active mode unlocks USSD/app channels in addition to SMS.
- `services/api-customer/README.md`: clarifies the portal is for enhanced control, not activation.

**Channel implication:** Passive mode delivers via SMS only — every MTN handset can receive an SMS, no app/USSD enrolment required. Active mode (registered on api-customer) adds USSD and in-app push.

**Revisit:** If subscriber complaints about unwanted notifications cross a defined threshold (>1 per 1000 alerts/month), introduce a one-tap opt-out via `STOP` reply to a recent fraud-alert SMS.
