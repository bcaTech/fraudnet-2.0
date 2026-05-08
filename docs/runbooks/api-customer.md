# Runbook — api-customer

## Purpose

Customer self-service surface. Single entry point for in-app fraud alerts, customer fraud reports, and self-service blocks.

## SLOs

| Endpoint | p99 |
|---|---|
| `/auth/request_otp` | < 500 ms (depends on OTP service) |
| `/auth/verify_otp` | < 100 ms |
| `/me/alerts` | < 250 ms |
| `/me/ott-alerts` | < 250 ms |
| `/me/blocked-domains` | < 300 ms (aggregation query) |
| `/me/report` | < 200 ms (Kafka produce) |
| `/me/report-url` | < 200 ms (Kafka produce) |

## Dashboards

- `rate(api_customer_otp_requested_total[1m])`
- `rate(api_customer_otp_verified_total[1m]) by (outcome)` — `wrong_code` rate is the abuse signal
- `rate(api_customer_reports_total[1m]) by (kind)`

## Alert: OTP `wrong_code` rate elevated

Either a misuse / brute-force attempt or an OTP delivery failure. Cross-reference with `request_otp` rate. If a single MSISDN is hammering verify, rate-limit at the gateway.

## Alert: report rate spike

Could be a real fraud campaign (good — the system is doing its job) or a customer-app bug (mass-submitting). Inspect `intel.events.v1` for distinct indicators vs. rate. The fraud team's review of `intel.events.v1` is what catches false positives.

## OTT self-service surface

Three Phase-3 endpoints surface OTT-specific fraud to the subscriber:
- `GET /me/ott-alerts` — alerts where `type='ott'` and either subject = customer MSISDN or `details.msisdn` = customer MSISDN. Includes domain + signal_kind so the customer sees what was flagged and why.
- `POST /me/report-url` — customer reports a suspicious URL. Forwarded to `intel.events.v1` with `indicator_kind='url'`.
- `GET /me/blocked-domains` — aggregated view of domains that were sinkholed on the customer's behalf, with first/last block timestamps and counts.

All three audit through `audit-lib.record()` with `purpose=fraud_prevention`. The reports endpoint validates URL length (≤2 kB) before producing to Kafka.

## OTP cutover (Phase 1 → security-team service)

1. Provision the OTP service URL + token in Vault.
2. Deploy api-customer with `OTP_SERVICE_URL` set. The service auto-selects `HttpOtpAdapter` when the URL is non-empty.
3. The dev adapter's deterministic `123456` no longer works — confirm the rollout against a canary MSISDN before broad release.

## Contacts

- Service team: @mtn-ghana/customer-experience
- OTP service: @mtn-ghana/security
- DPO: @mtn-ghana/dpo (any incident touching customer reports)
