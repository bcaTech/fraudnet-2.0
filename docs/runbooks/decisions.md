# Runbook — decisions

## Purpose

Routes scored signals and detected motifs to the right actuator tier per the YAML decision policy. Single point of policy enforcement for the platform.

## SLOs

| Metric | Target |
|---|---|
| Signal → action.tier* p99 | < 500 ms |
| Motif → action.tier* p99 | < 500 ms |
| Policy reload time | < 5 s (rolling restart) |
| Suppression false-suppress rate | < 0.1% |

## Dashboards

- `rate(decisions_evaluated_total[1m]) by (source, rule_id)`
- `rate(decisions_dispatched_total[1m]) by (tier, action)`
- `rate(decisions_suppressed_total[1m]) by (tier, action)` — should be ≪ dispatched count
- `rate(decisions_suppression_fallback_open_total[5m])` — Redis health canary

## Alert: dispatched_total drops to zero

Either the brain services have stopped emitting signals, or a policy change accidentally routed everything to the default rule. Inspect `/policy` summary, compare `evaluated_total{rule_id=...}` distribution against historical baseline.

## Alert: suppression_fallback_open rising

Redis is unreachable. Service is correctly failing open — duplicates may flow through but no decisions are lost. Stabilise Redis, no manual replay needed.

## Updating policy

1. Edit `services/decisions/policies/default.yaml`.
2. Open a PR with the diff. Reviewers: @mtn-ghana/decisions + @mtn-ghana/dpo (compliance review per CLAUDE.md §5.4).
3. After merge, ArgoCD rolls the deployment with the new policy mounted via ConfigMap.
4. The new `policy_version` and `fingerprint` appear on every dispatched decision; compliance can audit the change.

Phase 2 introduces hot-reload via inotify on the policy directory.

## Contacts

- Service team: @mtn-ghana/decisions
- DPO review required for policy YAML changes
- On-call: PagerDuty `fraudnet-decisions`
