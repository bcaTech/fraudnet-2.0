# ingest-momo

MoMo event listener. Translates MoMo BSS push events into canonical `MoMoEventV1` and publishes to Kafka topic `momo.events.v1`.

This is the most stable of the ingest services — it extends the existing FraudNet 1.0 MoMo integration (CLAUDE.md §5.1).

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/webhooks/momo` | Receives BSS event push. Auth: `X-MoMo-Signature` HMAC-SHA256. |
| `GET`  | `/health/live` | Liveness probe (process up). |
| `GET`  | `/health/ready` | Readiness probe (Kafka producer wired). |
| `GET`  | `/metrics` | Prometheus scrape. |

See [`docs/data-contracts/momo-bss.md`](../../docs/data-contracts/momo-bss.md) for the BSS payload contract.

## Local development

```bash
make infra-up                              # Kafka, Postgres, Redis, etc.
make kafka-topics-create                   # Apply momo.events.v1 + DLQ
make dev SERVICE=ingest-momo               # Hot-reload on :8100
```

Send a synthetic event:

```bash
curl -X POST http://localhost:8100/webhooks/momo \
  -H 'Content-Type: application/json' \
  -d '{"txn_id":"MTN-MOMO-LOCAL","event_type":"P2P","timestamp_ms":1700000000000,"sender_wallet_id":"W:1","recipient_wallet_id":"W:2","amount_minor":1000,"currency":"GHS","counterparty_kind":"wallet"}'
```

## Operational

- **Topic:** `momo.events.v1`. **DLQ:** `momo.events.dlq.v1`.
- **Partition key:** `sender_wallet_id` (or `recipient_wallet_id` for inbound-only). Guarantees event ordering within a wallet.
- **Idempotency:** dedupe via Redis SET NX with 24h TTL on derived `event_id`. Cache failure-open.
- **Auth:** HMAC-SHA256 over the request body using `MOMO_WEBHOOK_SHARED_SECRET`. Empty secret in dev = unauthenticated.
- **PII:** msisdn / wallet_id never logged raw — `obs.redact()` enforced by lint + runtime.

## SLOs

| Metric | Target |
|---|---|
| Webhook accept p99 | <100 ms |
| Kafka produce p99 | <30 ms |
| Availability | 99.95% monthly |

## Runbook

[`docs/runbooks/ingest-momo.md`](../../docs/runbooks/ingest-momo.md)
