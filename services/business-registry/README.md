# business-registry

Verified business sender registry. Looks up MSISDNs and short-codes
against an authoritative list of verified businesses (banks, MNOs,
e-merchants) so the scoring pipeline can apply a confidence discount —
verified senders almost never trigger fraud signals.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/businesses` | Register a business (status=`pending`) |
| `POST` | `/businesses/{id}/verify` | Flip status to `verified` |
| `POST` | `/businesses/{id}/msisdns` | Add a verified sending MSISDN |
| `POST` | `/businesses/{id}/shortcodes` | Add a verified sending short code |
| `GET` | `/businesses` | List (filterable by status) |
| `GET` | `/businesses/{id}` | Business detail |
| `GET` | `/lookup/msisdn/{msisdn}` | Verified-business lookup |
| `GET` | `/lookup/shortcode/{shortcode}` | Verified-shortcode lookup |
| `GET` | `/health/{live,ready}`, `/metrics` | k8s probes / scrape |

## Scoring pipeline integration

1. **brain-behavioural** wraps every Number score with a registry
   lookup. If the MSISDN is verified and the would-be signal is one of
   `voice.velocity_burst`, `device.imei_churn`, `sms.bulk_template` →
   the signal is suppressed and the score is multiplied by 0.1. The
   evidence dict is annotated with `verified_business=true` plus the
   business id/name.
2. **brain-content** runs the lookup against `short_code` on each MT
   SMS. Verified short-code senders' classifications are dropped before
   `to_signal()`.
3. **decisions** has a top-of-policy rule (`verified-business-suppress`)
   that catches any `verified_business=true` evidence reaching it from
   another producer and routes the signal directly to Tier-3
   investigation queue (no Tier-1/Tier-2 actuation).

## Caching

- Server side: Redis DB 5, 5-minute positive TTL, 60-second negative TTL.
- Client side (`HttpBusinessRegistryClient`): in-process LRU cache.

## Onboarding flow

1. Operator submits a `POST /businesses` (status=`pending`) with the
   business's name + GH registration number.
2. Compliance verifies the registration and adds MSISDNs / short-codes.
3. Operator submits `POST /businesses/{id}/verify` once due-diligence is
   complete.

The `business_false_positives` table is populated nightly by the
api-noc job that joins the alerts table on verified-business MSISDNs and
short-codes — it surfaces in the api-noc dashboard via
`GET /api/noc/false-positives/businesses`.

## Runbook

[`docs/runbooks/business-registry.md`](../../docs/runbooks/business-registry.md)
