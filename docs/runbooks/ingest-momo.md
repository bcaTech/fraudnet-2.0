# Runbook — ingest-momo

## Purpose

Translates MoMo BSS push events into the canonical `MoMoEventV1` and publishes to Kafka topic `momo.events.v1`. Inline-adjacent: a stalled ingest-momo blocks every downstream MoMo signal.

## SLOs

| Metric | Target |
|---|---|
| Webhook accept p99 | < 100 ms |
| Kafka produce p99 | < 30 ms |
| Availability | 99.95% monthly |
| Consumer lag | n/a (this is a producer) |

## Dashboards

- Grafana → FraudNet → ingest-momo
- Useful queries:
  - `rate(ingest_momo_webhook_received_total[1m])`
  - `rate(ingest_momo_webhook_rejected_total[5m]) by (reason)`
  - `histogram_quantile(0.99, sum by (le) (rate(fraudnet_request_duration_seconds_bucket{service="ingest-momo"}[5m])))`
  - `rate(fraudnet_kafka_messages_failed_total{topic="momo.events.v1"}[5m])`

## Alert: webhook 5xx rate elevated

1. Check the `reason` label on `ingest_momo_webhook_rejected_total`.
2. If `kafka_delivery_failed`: check broker health (`kafka-broker-api-versions`); check `momo.events.v1` partition leadership.
3. If sustained, fail open at the BSS layer — operations team has the runbook to switch BSS to dual-publish into `ingest-momo-staging` while we recover.

## Alert: idempotency cache fallback open

Metric: `ingest_momo_idempotency_fallback_open_total`.

1. Redis at `REDIS_URL` is unreachable. Check the Redis pod / instance health.
2. While Redis is down, the service falls open — every BSS event is processed even if a duplicate. Stream-graph and stream-features tolerate duplicates; the impact is minor cost amplification.
3. After recovery, no manual catch-up is needed; the cache will repopulate on the next event window.

## Alert: schema registry unreachable

The producer fails closed at startup. Pod restarts. Investigate Schema Registry health; ensure the `momo.events.v1` subject is registered.

## Deploy

GitOps via ArgoCD. Canary 5% → 25% → 50% → 100%. Rollback is `argocd app rollback ingest-momo {revision}`.

## Contacts

- Service team: @mtn-ghana/momo + @mtn-ghana/ingestion
- On-call: PagerDuty schedule `fraudnet-ingest`
- Escalation: programme-lead → MTN MoMo BSS team
