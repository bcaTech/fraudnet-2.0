# decisions

Decision orchestrator. Consumes `fraud.signals.v1` and `motifs.detected.v1`, applies a YAML-driven policy, and fans out `DecisionDispatchedV1` to:

- `decisions.dispatched.v1` — audit trail (consumed by compliance)
- `action.tier{1,2,3}.v1` — per-tier actuator topics (consumed by `action-tier{1,2,3}`)

Per CLAUDE.md §5.4 and [DECISIONS.md](../../DECISIONS.md) D-003: policy is YAML so regulator-relevant decisions are reviewable without code changes.

## Policy

Default policy lives at `policies/default.yaml`. A rule is the first matching predicate set. Match keys:

- `signal_kind` — exact match
- `motif` — exact match
- `severity_in: [list]` — severity ∈ list
- `score_gte: <float>` — score ≥ threshold
- `subject_kind` — exact match

Effect keys:

- `action: <str>` — emitted as `DecisionDispatchedV1.action`
- `tier: tier1 | tier2 | tier3`
- `suppression_window_s: <int>` — TTL on `(suppression_key, action)` dedup

Anything unmatched falls to `default:` (Tier-3 investigation queue).

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/health/{live,ready}` | k8s probes |
| `GET`  | `/policy` | Loaded policy summary (id, version, fingerprint, rules) |
| `GET`  | `/metrics` | Prometheus scrape |

## Operational

- **Inputs:** `fraud.signals.v1`, `motifs.detected.v1`
- **Outputs:** `decisions.dispatched.v1` (audit) + `action.tier{1,2,3}.v1` (actuators)
- **Suppression:** Redis SET-NX with rule-defined TTL. Fail-open if Redis unreachable (better dispatch a duplicate than lose a decision).
- **Policy hot-reload:** Phase 2 — for now, restart the deployment to pick up policy changes.

## Runbook

[`docs/runbooks/decisions.md`](../../docs/runbooks/decisions.md)
