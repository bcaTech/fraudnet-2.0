# Runbook — stream-graph

## Purpose

Materialises the production graph in Memgraph. Per-event (not windowed); a stalled stream-graph means the graph slowly drifts from reality.

## SLOs

| Metric | Target |
|---|---|
| Kafka → Memgraph p95 | < 30 s (sub-minute consistency target per §5.2) |
| Consumer lag | < 50 k per partition |
| `graph.mutations.v1` produce p99 | < 30 ms |

## Dashboards

- `rate(stream_graph_mutations_translated_total[1m]) by (source_topic, op)`
- `fraudnet_graph_mutations_dropped_total` — non-zero means buffer overflow or flush failure
- `fraudnet_graph_query_seconds` — Memgraph write latency

## Alert: graph_mutations_dropped_total rising

Buffer overflow (`reason="buffer_full"`) or flush failure (`reason="flush_failed"`).

1. Check Memgraph health: `bolt://memgraph:7687` reachable? Memory usage?
2. Increase `GRAPH_BUFFER_MAX` if a temporary write spike (e.g. probe replay catching up).
3. If sustained, scale Memgraph vertically — graph writes are memory- and CPU-bound.

## Alert: Memgraph cluster lost (in-memory state gone)

Memgraph has no durability outside its 6h snapshots. If a restart loses state:

1. Memgraph re-bootstraps from its latest snapshot.
2. Replay missing events from Kafka via `tools/replay --topic voice.events.v1,sms.events.v1,momo.events.v1 --since <snapshot_time>`.
3. Replay output goes to a temporary consumer-group prefix; verify, then promote.

## Alert: graph.mutations.v1 produce lag

The control topic should be milliseconds-fresh. Lag indicates either Schema Registry pressure or Kafka broker issues. Inspect `fraudnet_kafka_messages_failed_total{topic="graph.mutations.v1"}`.

## Phase 2 cutover (PyFlink)

Same procedure as stream-features: parallel-run the PyFlink job against a different consumer group, verify graph state convergence on a tenant-scoped subgraph, swap on cutover.

## Contacts

- Service team: @mtn-ghana/streaming + @mtn-ghana/graph
- On-call: PagerDuty `fraudnet-streaming`
