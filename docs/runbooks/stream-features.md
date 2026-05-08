# Runbook — stream-features

## Purpose

Window-aggregates voice / SMS / MoMo events into the Aerospike feature store. Inline-tier services read from Aerospike with a 1 ms p99 budget; if features are stale, scoring is degraded.

## SLOs

| Metric | Target |
|---|---|
| End-to-end Kafka → Aerospike p99 | < 5 s |
| Consumer lag (any partition) | < 50 k |
| Late-event drop rate | < 0.1 % of events |

## Dashboards

- `rate(stream_features_events_processed_total[1m]) by (topic)`
- `rate(fraudnet_kafka_messages_dlq_total{topic=~"voice|sms|momo.events.v1"}[5m])`
- `rate(fraudnet_feature_write_seconds_count[1m]) by (entity_kind)`

## Alert: late_events_dropped rising

The pipeline is dropping events older than the 30 s lateness allowance. Possible causes:

1. Probe vendor batching events out of order.
2. Kafka consumer falling behind a partition; clock skew on the writer.
3. A partition rebalanced and the new owner's high-water timestamp is stale.

Investigate per-topic. If sustained, raise lateness allowance via env (`FEATURE_LATENESS_MS`) — but flag with the data-science team because it changes downstream feature semantics.

## Alert: Aerospike write p99 elevated

Check Aerospike namespace memory pressure (`asadm -e info`). The dev pod is sized for ~2 GB; production should be horizontally scaled and TTL-tuned.

## Restart / replay

The pipeline is in-memory; restart loses transient window state. Acceptable: features rebuild from Kafka replay within ~1 hour for the longest window. Aerospike retains the last-flushed snapshot for 24 h. Manual replay from the lakehouse via `tools/replay` if a longer recovery is needed.

## Phase 2 cutover (PyFlink)

When migrating from the standalone runner to the PyFlink job:
1. Submit the PyFlink job in parallel against a different consumer group.
2. Compare feature snapshots in Aerospike under `numbers_pf` / `wallets_pf` set names.
3. Promote by switching `STREAM_FEATURES_GROUP` env on the runner to a new value (so the old runner stops consuming on cutover).

## Contacts

- Service team: @mtn-ghana/streaming
- On-call: PagerDuty `fraudnet-streaming`
