# action-tier2

Near-real-time actuators (CLAUDE.md §5.4). Consumes `action.tier2.v1`, dispatches to customer-facing backends with a 2 s timeout.

## Action map

| Action | Subject | Backend |
|---|---|---|
| `customer.alert_smishing` | number | Customer notify (SMS / push) |
| `customer.do_i_know_you_prompt` | number | App prompt API |
| `momo.review_limit` | wallet | MoMo BSS limit-review queue |
| `safeguard.enroll` | number / wallet | SafeGuard auto-enrollment |

## Operational

- **Input:** `action.tier2.v1`
- **Latency tolerance:** seconds-to-minutes (per spec). 2 s HTTP timeout.
- **Empty backend URL ⇒ NoopActuator** (logs only) so staging environments without the customer notify or SafeGuard endpoints can still soak the rest of the pipeline.

## Endpoints

| Path | Purpose |
|---|---|
| `GET /health/{live,ready}` | k8s probes |
| `GET /metrics` | Prometheus scrape |
| `GET /registry` | Loaded action map for triage |

## Runbook

[`docs/runbooks/action-tier2.md`](../../docs/runbooks/action-tier2.md)
