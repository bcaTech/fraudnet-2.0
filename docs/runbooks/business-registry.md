# Runbook — business-registry

## Purpose

Authoritative list of verified business senders. Reads back to the
scoring pipeline so verified MSISDNs and short-codes don't trip Tier-1
or Tier-2 actuators.

## SLOs

| Metric | Target |
|---|---|
| `/lookup/msisdn` p99 | < 5 ms (Redis hit), < 20 ms (DB miss) |
| `/lookup/shortcode` p99 | < 5 ms |
| Cache hit ratio | > 95% steady-state |

## Dashboards

- `rate(business_registry_lookups_total[1m]) by (kind, matched, verified)`
- `rate(business_registry_ops_total[1h]) by (op)`
- `rate(brain_behavioural_verified_discount_total[5m]) by (entity_kind)`
- `rate(brain_content_verified_short_code_suppressed_total[5m])`

## Alert: verified business is being alerted on

A verified business showing up in
`/api/noc/false-positives/businesses` with a high `fp_rate` indicates
either:

1. The signal was emitted with `verified_business=false` evidence
   (registry lookup failed at score time → check brain-behavioural
   logs for `registry_lookup_failed`).
2. The verification status changed but the cache wasn't invalidated.
   Force-evict: `redis-cli -n 5 KEYS 'biz:*' | xargs redis-cli -n 5 DEL`.
3. The signal came from a path that doesn't apply the discount
   (e.g. motif detection — investigate at `decisions` policy level).

## Onboarding a new business

```
POST /businesses
{ "name": "Ecobank Ghana", "registration_number": "GH-CI-12345" }
→ 201 { "id": "<uuid>", "status": "pending" }

POST /businesses/<id>/msisdns  { "msisdn": "+233231234567" }
POST /businesses/<id>/shortcodes  { "shortcode": "ECOBANK" }

POST /businesses/<id>/verify
→ 200 { "status": "verified", "verified_at": "..." }
```

Cache is invalidated on verify and on any add. The brain-* clients
cache positive lookups for ~5 min — a verified business's discount
takes effect at the next scoring window after onboarding.

## Migrations

`migrations/0001_initial.sql` — businesses, business_msisdns,
business_shortcodes, business_false_positives. Apply via the standard
Phase-1 migration runner.

## Contacts

- Service team: @mtn-ghana/fraud-eng + @mtn-ghana/compliance
- Onboarding ops: @mtn-ghana/business-onboarding
