# action-tier1

Inline actuators on the **sub-200 ms path** (CLAUDE.md §5.4). Consumes `action.tier1.v1`, dispatches to backend systems with a 100 ms HTTP timeout.

## Action map

| Action | Subject kind | Backend |
|---|---|---|
| `volte.tag_suspected_spam` | number | IMS-core SIP-header rewrite |
| `url.block` | url | DNS sinkhole push |
| `sms.block` | number | SMSC outbound block list |
| `momo.send_with_care` | wallet | MoMo BSS friction prompt |

## Operational

- **Input:** `action.tier1.v1` (DecisionDispatchedV1)
- **Backends:** HTTP, configured via env URLs. Empty URL ⇒ `NoopActuator` (logs only) — useful for staging environments where the IMS or DNS sinkhole isn't yet wired.
- **Timeout:** 100 ms per actuator call. On timeout we mark `failed` and surface to the feedback loop; the upstream replay path handles recovery.
- **Outcome emission:** `actions.taken.v1` (Phase-2 wiring; for Phase 1 we emit only via metrics + structured logs).

## Endpoints

| Path | Purpose |
|---|---|
| `GET /health/{live,ready}` | k8s probes |
| `GET /metrics` | Prometheus scrape |
| `GET /registry` | List of actuator action names — handy for incident triage |

## SLOs

| Metric | Target |
|---|---|
| Action.tier1 → backend p99 | < 150 ms (leaves 50 ms for the rest of the 200 ms inline budget) |
| Actuator failure rate | < 0.5% under steady state |

## Runbook

[`docs/runbooks/action-tier1.md`](../../docs/runbooks/action-tier1.md)
