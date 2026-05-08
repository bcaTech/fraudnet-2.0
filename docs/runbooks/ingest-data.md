# Runbook — ingest-data

## Purpose

DNS resolver + IPDR push receiver. Both feed the canonical `DataEventV1` on `data.events.v1`. Subscriber attribution on DNS is best-effort (resolver maps source IP → MSISDN where it can); IPDR is always attributed.

## SLOs

| Metric | Target |
|---|---|
| Push accept p99 | < 50 ms |
| Kafka produce p99 | < 30 ms |
| Availability | 99.9% |
| Unattributed-DNS rate | < 5% sustained |

## Dashboards

- `rate(ingest_data_received_total[1m]) by (source, kind)`
- `rate(ingest_data_rejected_total[5m]) by (source, reason)`
- `rate(ingest_data_unattributed_total[5m])` — high values suggest a resolver-side IP→MSISDN lookup failure
- `rate(ingest_data_duplicates_total[5m])` — replay storms from collector retries

## Alert: ingest-data DNS unattributed rate >20% for 10m

The resolver vendor's IP→MSISDN attribution job is degraded. Flag the vendor on-call. Stream-features still computes domain-level reputation from unattributed traffic so the platform isn't blind, but per-subscriber DNS rate features will under-report until attribution recovers.

## Alert: ingest-data parse_error rate spike

Resolver or IPDR vendor pushed a payload format change. Inspect the DLQ via `tools/replay`; update the adapter or vendor shim. Common offenders: vendors switching `event_type` casing or moving from `dst_ip` to `destination_address`.

## Alert: idempotency_fallback_open rising

Redis is unavailable. The service fails open (better duplicates downstream than dropped events). Investigate Redis health; downstream stream-features dedupes on `event_id` so duplicate cost is bounded.

## Routine: weekly suspicious-domain review

`brain-content` exports a weekly list of domains it flagged as newly registered or phishing-like. Cross-check against the IPDR domain top-N to confirm we are seeing the traffic we expect; mismatches mean a DPI sampling issue.

## Vendor shim addition

For a new resolver / collector vendor:
1. Add a translation in `adapter.py` (parse vendor format → `DnsPushEvent` / `IpdrPushEvent`).
2. Document under "Vendor variants" in `docs/data-contracts/dns-push.md` or `ipdr-push.md`.
3. Set `DATA_DNS_RESOLVER_ID=<vendor>` or `DATA_IPDR_COLLECTOR_ID=<vendor>` for the deployment.

## Contacts

- Service team: @mtn-ghana/ingestion + @mtn-ghana/data-platform
- DPO: @mtn-ghana/dpo (any privacy-related escalation; DNS qnames + IPDR destinations include browsing intent)
