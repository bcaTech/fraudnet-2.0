# Runbook — ingest-voice

## Purpose

Vendor-neutral SS7/Diameter/IMS probe receiver. Inline-adjacent — a stalled ingest-voice blocks every downstream voice signal. Probe → Kafka p99 budget is 30 ms.

## SLOs

| Metric | Target |
|---|---|
| Probe accept p99 | < 30 ms |
| Kafka produce p99 | < 30 ms |
| Availability | 99.99% (inline-adjacent) |

## Dashboards

- Grafana → FraudNet → ingest-voice
- `rate(ingest_voice_probe_received_total[1m])`
- `rate(ingest_voice_probe_rejected_total[5m]) by (reason)`
- `histogram_quantile(0.99, sum by (le) (rate(fraudnet_request_duration_seconds_bucket{service="ingest-voice"}[5m])))`

## Alert: probe accept p99 > 30 ms

1. Inspect Kafka producer queue: are we waiting on broker acks? Check `fraudnet_kafka_messages_failed_total`.
2. Check Schema Registry health (`http://schema-registry:8081/subjects`). Producer fails closed if registry is unreachable; pod restarts.
3. Vendor flap? Look at `ingest_voice_probe_rejected_total{reason="parse_error"}` — a sudden spike usually means the vendor pushed a payload format change.
4. If sustained, scale horizontally: `kubectl scale deploy ingest-voice --replicas=N`.

## Alert: ingest_voice_idempotency_fallback_open_total rising

Redis is unreachable. Service falls open (allows duplicates). Check Redis pod; no manual catch-up needed after recovery.

## Vendor cutover

When the probe RFI lands and vendor selection changes:
1. Add a vendor shim in `adapter.py` translating the vendor's native format to `GenericProbeEvent`.
2. Deploy with `VOICE_VENDOR_ID=<vendor>` env var; this surfaces in `event.source` for downstream debugging.
3. Run synthetic loadgen against staging (`tools/load-gen --topic voice.events.v1`).

## Contacts

- Service team: @mtn-ghana/ingestion + @mtn-ghana/network
- On-call: PagerDuty `fraudnet-ingest`
