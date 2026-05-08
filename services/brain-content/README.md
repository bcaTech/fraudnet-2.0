# brain-content

SMS content classification — URL reputation + template patterns. Phase 1 heuristic; Phase 2 swaps to a fine-tuned sentence-transformer + classifier head behind the same `ContentClassifier` interface (CLAUDE.md §5.3).

## Two paths

- **Fast** — known-bad `body_hash` / `template_hash` lookup (sub-millisecond).
- **Model** — body-bearing classifier: URL reputation + scam-keyword heuristic.

Body access is **purpose-gated** at ingest-sms (`SMS_ALLOW_BODY_CAPTURE`). When body is `None`, only the fast path runs; downstream alerts come from the hash-based signals.

## Phase 1 signals

| Signal | Trigger | Severity |
|---|---|---|
| `sms.known_bad_body` | body_hash in blocklist | critical |
| `sms.known_bad_template` | template_hash in blocklist | high |
| `sms.malicious_url` | URL reputation hit (exact or subdomain) | high (medium for subdomain) |
| `sms.template_smishing` | ≥3 distinct scam keywords | medium |

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/score/sms` | Sync classification |
| `GET`  | `/health/{live,ready}` | k8s probes |
| `GET`  | `/metrics` | Prometheus scrape |

## Operational

- **Inputs:** `sms.events.v1` (MT only — MO and DR skipped)
- **Output:** `fraud.signals.v1` keyed by sender msisdn
- **Suppression key:** `<tenant>:number:<sender>:<signal_kind>`

## Runbook

[`docs/runbooks/brain-content.md`](../../docs/runbooks/brain-content.md)
