# intel-repository

Shared fraud intelligence database. Auto-populated from
`fraud.signals.v1` and `actions.taken.v1`; queried by brain-* services
during scoring as an enrichment source.

## Kinds

| Kind | What it is | Source |
| --- | --- | --- |
| `suspect_number` | MSISDNs flagged for fraud | brain-behavioural / -content / -agent-fraud / aml-watchlist |
| `high_risk_destination` | International ranges with elevated fraud | brain-behavioural geo signals |
| `unallocated_range` | Ranges not assigned to any operator (spoof source) | analyst contribution |
| `scam_template` | SMS template hashes from brain-content | brain-content classifier |
| `spoof_indicator` | CLIs that failed validation / appear in fraud contexts | brain-content + analyst |
| `agent_risk` | Composite risk scores from brain-agent-fraud | brain-agent-fraud |

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET  | `/intel/lookup/{kind}/{identifier}` | Sub-ms hot lookup (service-to-service) |
| GET  | `/intel/suspect-numbers` | Paginated; `min_score=`, `page=`, `limit=` |
| GET  | `/intel/high-risk-destinations` | International ranges |
| GET  | `/intel/unallocated-ranges` | Unassigned ranges |
| GET  | `/intel/scam-templates` | Template hash clusters |
| GET  | `/intel/spoof-indicators` | CLI validation failures |
| GET  | `/intel/agent-risk` | Agent composite scores |
| POST | `/intel/contribute` | Manual analyst contribution |
| GET  | `/intel/stats` | Per-kind counts + 24h activity |

The lookup path is open to service callers; production gates by network
policy. The investigator listing endpoints require `FRAUD_*` roles.

## TTL + decay

Entries expire after 90 days of no activity by default
(`INTEL_TTL_DEFAULT_S`). Tighter defaults for fast-moving kinds:
30 days for `scam_template`, 7 days for `spoof_indicator`. Risk scores
are monotonic over the active life of an entry — fresh evidence boosts
the score; the only way scores fall is via expiration + re-creation.

## Hot path

`HOT_KINDS` (suspect_number, spoof_indicator, scam_template) lookups
are Redis-cached. Both positive and negative cache entries are stored,
the latter at a shorter TTL — the scoring path queries on every event
and the absence-cache prevents Postgres flooding on the
"is this MSISDN suspect?" common case.
