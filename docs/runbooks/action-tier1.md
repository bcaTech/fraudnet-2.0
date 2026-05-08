# Runbook — action-tier1

## Purpose

Inline actuators. Sub-200 ms total budget; this service is on the critical path for Tier-1 actions (VoLTE tag, URL block, MoMo friction). Operationally the most sensitive service in the platform.

## SLOs

| Metric | Target |
|---|---|
| Action handler p99 | < 150 ms |
| Actuator failure rate | < 0.5% |
| Consumer lag (action.tier1.v1) | < 1 k |

## Dashboards

- `rate(action_tier1_handled_total[1m]) by (action, outcome)`
- `rate(action_tier1_invocations_total[1m]) by (action, outcome)`
- p99 latency from emitted action_taken metadata vs. consumer commit time

## Alert: actuator failure rate elevated

1. Check the affected backend (IMS / DNS sinkhole / SMSC / MoMo BSS). The actuator surfaces the HTTP status / timeout in `error`.
2. If a backend is unreachable, this service fails fast (100 ms timeout) — duplicates from upstream retries are absorbed by the per-action idempotency at the backend.
3. If a single backend is degraded, decisions still flow to others. Consider tightening the relevant policy rule's `suppression_window_s` to reduce load while the backend recovers.

## Alert: tier1 dispatched count drops to zero

1. Inspect the upstream `decisions` service: `decisions_dispatched_total{tier="tier1"}`.
2. If decisions is healthy but tier1 is silent, check the Kafka consumer for `action.tier1.v1` partition assignment.

## Deploy gate

Per CLAUDE.md §9.2: action-tier1 has an additional 30-minute soak at 5% traffic before any wider rollout. Do not bypass.

## Contacts

- Service team: @mtn-ghana/decisions + @mtn-ghana/network
- IMS backend: @mtn-ghana/network
- DNS sinkhole: @mtn-ghana/security
- SMSC: @mtn-ghana/messaging
- On-call: PagerDuty `fraudnet-tier1` (paged on any failure rate spike)
