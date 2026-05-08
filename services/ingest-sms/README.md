# ingest-sms

SMSC event listener. Translates SMSC pushes into canonical `SmsEventV1` and publishes to Kafka topic `sms.events.v1`.

Body content access is **purpose-gated** (CLAUDE.md §5.1) — `SMS_ALLOW_BODY_CAPTURE=false` by default, even when bodies arrive. `body_hash` and `template_hash` are always emitted so downstream `brain-content` can dedup and cluster without re-reading bodies.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/smsc/push` | SMSC push (HMAC `X-SMSC-Signature`) |
| `GET`  | `/health/{live,ready}` | Probes |
| `GET`  | `/metrics` | Prometheus scrape |

See [`docs/data-contracts/smsc-push.md`](../../docs/data-contracts/smsc-push.md).

## Local dev

```bash
make infra-up && make kafka-topics-create
make dev SERVICE=ingest-sms
```

## Operational

- **Topic:** `sms.events.v1`. **DLQ:** `sms.events.dlq.v1`.
- **Partition key:** `sender` MSISDN — keeps a sender's burst for template clustering.
- **Idempotency:** Redis 2h TTL; SMSC redelivery is bursty.
- **Body capture:** DISABLED by default. Enable only with DPO + legal sign-off via `SMS_ALLOW_BODY_CAPTURE=true`. Audited.
- **Template hash:** SHA-256 over body with `<MSISDN>`, `<AMOUNT>`, `<NUM>` token replacement — identical scam templates collide regardless of variable parts.

## SLOs

| Metric | Target |
|---|---|
| Push accept p99 | < 50 ms |
| Kafka produce p99 | < 30 ms |
| Availability | 99.95% |

## Runbook

[`docs/runbooks/ingest-sms.md`](../../docs/runbooks/ingest-sms.md)
