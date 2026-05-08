# Runbook — brain-behavioural

## Purpose

Behavioural scoring of Number and Wallet entities. Phase 1 heuristic; Phase 2 LightGBM via model registry.

## SLOs

| Metric | Target |
|---|---|
| Sync `/score/number` p99 | < 5 ms (cache miss tolerated up to 25 ms) |
| Async signal emission p95 | < 2 s from graph.mutations.v1 |
| Availability | 99.9% |

## Dashboards

- `rate(brain_behavioural_scored_total[1m]) by (entity_kind, fired)`
- `rate(brain_behavioural_features_missing_total[5m]) by (entity_kind)` — should trend to 0 once stream-features catches up

## Alert: features_missing rate elevated

`stream-features` is lagging or down. Check that runner; this service depends on it for upstream feature snapshots.

## Alert: scored_total{fired="true"} drops to zero

Either no real fraud signal in the window (good) or scorer threshold drift (bad). Compare against historical baseline. If thresholds need adjusting, change `HeuristicScorer` constants and ship a new release — the model_version in emitted signals updates automatically.

## Phase 2 cutover (LightGBM)

1. Data science team publishes a `behavioural-lightgbm-<date>.pkl` artefact to the model registry.
2. Deploy a new `LightGBMScorer` implementation of the `Scorer` interface; same constructor signature.
3. Run champion (heuristic) and challenger (LightGBM) in parallel by tagging their emitted signals with distinct `model_id`. Decisions service can A/B them via policy.
4. Promote on metric review.

## Contacts

- Service team: @mtn-ghana/data-science + @mtn-ghana/fraud-engineering
- On-call: PagerDuty `fraudnet-brain`
