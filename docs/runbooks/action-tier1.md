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

## Alert: sinkhole local allow-list hit

`action_tier1_sinkhole_local_allow_list_hits_total{matched="true"}` should be at zero. Any non-zero rate means a Tier-1 decision tried to sinkhole a domain that is on the actuator-side defensive allow-list (MTN, BoG/NCA, major OTT). The actuator suppressed the call — but the decision should not have fired at all.

1. Inspect the offending decision via the `decision_id` in the WARN log.
2. Walk back to the policy rule that fired and confirm whether the domain match was overly broad.
3. If url-intel's allow-list and the local list have drifted (we expect them to be a strict superset on the local side), reconcile; both lists are CSV-loaded from env so this is a config delta, not a code change.

## OTT URL blocking — `dns.sinkhole` action

OTT URL takedown is wired through a `dns.sinkhole` action distinct from the legacy `url.block`. Both actions back to the same `DnsSinkholeActuator`; the split exists so policies can pick "sinkhole at the resolver" explicitly without binding to legacy URL-block semantics. Allow-list defence is layered:

1. Local actuator-side list (`SINKHOLE_LOCAL_ALLOW_LIST`) — short-circuits before any network call. Logs a WARN if hit.
2. url-intel `/blocklist/add` — authoritative source; returns `added=false, reason="allow_listed"`. Suppressed outcome.
3. Sinkhole resolver call (`SinkholeApiClient`) — only reached after both allow-lists have cleared the domain.

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
