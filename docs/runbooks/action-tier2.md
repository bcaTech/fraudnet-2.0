# Runbook — action-tier2

## Purpose

Customer-facing actuators on the NRT path. Tolerates seconds-to-minutes latency.

## SLOs

| Metric | Target |
|---|---|
| Action handler p99 | < 2 s |
| Actuator failure rate | < 1% |
| Consumer lag | < 5 k |

## Dashboards

- `rate(action_tier2_handled_total[1m]) by (action, outcome)`
- `rate(action_tier2_invocations_total[1m]) by (action, outcome)`

## Alert: customer-alert backend timing out

1. Check the customer notify service health (separate team owns it).
2. Decisions can keep flowing — the consumer lag will rise but actions are not on the inline path. Scale tier-2 horizontally if the lag does not recover within 15 minutes.

## Alert: safeguard auto-enroll spike

If an unusually large fraction of customers are being auto-enrolled, this may indicate a bad policy rollout or a brain-content false-positive surge. Compare against historical baseline. Roll back the most recent decisions YAML if elevated.

## Contacts

- Service team: @mtn-ghana/decisions + @mtn-ghana/customer-experience
- Customer notify: @mtn-ghana/customer-experience
- SafeGuard: @mtn-ghana/safeguard
- On-call: PagerDuty `fraudnet-tier2`
