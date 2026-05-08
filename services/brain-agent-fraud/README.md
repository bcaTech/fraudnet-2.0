# brain-agent-fraud

Dedicated MoMo agent / merchant fraud detector. Five patterns, each with
its own threshold config. Consumes `momo.events.v1` and publishes to
`fraud.signals.v1` with agent-specific `signal_kind` values.

## Detectors

| `signal_kind` | Pattern |
| --- | --- |
| `agent.commission_farming` | Same agent + same customer cycling cash-in/cash-out repeatedly |
| `agent.split_txn` | Large amount broken into pieces under monitoring threshold |
| `agent.phantom_customer` | Multiple transactions against zero-history counterparties |
| `agent.collusion` | Multiple agents sharing devices or moving funds in coordinated patterns |
| `agent.float_manipulation` | Excess float OR ≥ N internal transfers between agent-owned accounts |

Each detection becomes a SignalEventV1 with XAI attached
(`feature_contributions` + `explanation_text`) so downstream surfaces
the reasoning to the analyst without further work.

## Endpoints

| Method | Path | Description |
| --- | --- | --- |
| GET | `/agents/risk-ranking` | Top agents by composite risk score |
| GET | `/agents/{agent_id}/profile` | Full per-agent profile with pattern breakdown |
| GET | `/agents/commission-anomalies` | Agents dominated by commission_farming |

Roles: any `FRAUD_*` for ranking + profile; commission-anomalies
restricted to `FRAUD_ANALYST`+ since the pattern is investigation-grade.

## Composite score

`composite_score = 1 - ∏(1 - per_pattern_score)` — independent patterns
each adding evidence; capped at 1.0; a single very high pattern
dominates.

## Decay

Profiles decay 10 %/hour by default — a quiet agent's score drifts back
to zero over ~10 hours. New detections take the higher of decayed-old
vs new.

## Cohort + history adapters

`detect_collusion()` takes a `CohortLookup` adapter (provided by
brain-graph in production); `detect_phantom_customer()` takes a
`CounterpartyHistory` adapter (Postgres / feature store). Both default
to no-op implementations so the service can run in isolation for dev.
