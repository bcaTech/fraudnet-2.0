# Runbook — url-intel

## Purpose

Real-time URL threat intel. Backs the Tier-1 `url.block` actuator and
serves the DNS sinkhole's `/blocklist/export` poll. Subscribes to
`fraud.signals.v1` for brain-content's URL-related signals.

## SLOs

| Metric | Target |
|---|---|
| `/blocklist/check` p99 | < 5 ms |
| `/blocklist/export` p99 | < 50 ms (Redis SMEMBERS over 100k entries) |
| Signal-to-blocklist lag p95 | < 5 s from `fraud.signals.v1` |

## Dashboards

- `rate(url_intel_checks_total[1m]) by (blocked, allow_listed)`
- `rate(url_intel_signals_ingested_total[5m]) by (signal_kind, outcome)`
- `rate(url_intel_feed_imports_total[1h]) by (feed, outcome)`
- `redis_keyspace_size{db="4"}` (approx blocklist size)

## Alert: blocklist size regression

If `/blocklist/export.count` drops sharply, either:
1. Redis lost data (memory eviction or restart without persistence) →
   re-run feed import jobs (`tools/url-intel/reseed.sh`).
2. The signals listener stopped consuming → check
   `rate(url_intel_signals_ingested_total[5m])`.

## Alert: allow-listed domains being blocked

This should be impossible — `Blocklist.add()` rejects allow-listed
domains. If an allow-listed domain is showing up in `/blocklist/export`,
something bypassed the API. Inspect `urlintel:meta:<domain>` for
`source`. Open an incident.

## Updating the allow-list

1. Update `URL_INTEL_ALLOW_LIST` env / ConfigMap.
2. Roll-restart `url-intel` to pick up the change.
3. Run a one-shot reconciliation: `for d in $(NEW_ALLOW); do
   curl -X POST .../blocklist/remove -d "{\"domain\":\"$d\"}"; done`
   to evict any pre-existing entries.

## Tier-1 integration

`action-tier1` binds `url.block` to `DnsSinkholeActuator` when
`URL_INTEL_URL` is set. The actuator:
1. POSTs to `/blocklist/add` with `source=action-tier1:<decision_id>`.
2. If url-intel reports `allow_listed`, marks the result `suppressed`
   (deliberate non-block).
3. Otherwise POSTs to `URL_BLOCK_URL` (the DNS resolver block endpoint).

## Phase 2

- DNS resolver pull-mode replaces the per-decision push to URL_BLOCK_URL;
  the resolver polls `/blocklist/export` every 60s.
- A janitor job evicts expired meta-keys' domains from the SET.

## Contacts

- Service team: @mtn-ghana/messaging + @mtn-ghana/threat-intel
- DNS resolver: @mtn-ghana/network
