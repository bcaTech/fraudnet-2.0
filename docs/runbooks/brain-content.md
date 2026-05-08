# Runbook — brain-content

## Purpose

SMS content classification. Subscribes to `sms.events.v1`, publishes to `fraud.signals.v1`. Phase 1 heuristic; Phase 2 transformer-backed.

## SLOs

| Metric | Target |
|---|---|
| Sync `/score/sms` p99 | < 30 ms (model path); < 1 ms (fast hash path) |
| Async signal emission p95 | < 2 s from sms.events.v1 |
| URL reputation lookup p99 | < 1 ms |

## Dashboards

- `rate(brain_content_classified_total[1m]) by (fired, with_body)`
- `rate(fraudnet_kafka_messages_dlq_total{topic="sms.events.v1"}[5m])`

## Alert: with_body=false trending high in production

Either `SMS_ALLOW_BODY_CAPTURE` is unintentionally off, or a configuration drift. Body capture is required for the model path — the hash path still fires on known-bad lists, so this isn't a critical outage, but coverage is degraded.

## Updating the URL blocklist

1. Update the `BRAIN_CONTENT_BAD_DOMAINS` env / ConfigMap.
2. Rolling-restart the deployment to load the new list.
3. (Phase 2) When the production malicious-URL DB lands, swap `StaticBlocklist` for the DB-backed `ReputationLookup` implementation.

## Phase 2 cutover (transformer model)

1. Ship a `TransformerContentClassifier(ContentClassifier)` implementation in a new release.
2. Run heuristic + transformer in parallel via distinct `model_id` on emitted signals.
3. Decisions service can A/B test based on signal `model_id` in policy YAML.

## Contacts

- Service team: @mtn-ghana/data-science + @mtn-ghana/messaging
- DPO: @mtn-ghana/dpo (any body-capture incident)
