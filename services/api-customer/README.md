# api-customer

Customer self-service API. MSISDN-OTP authentication; tenant-of-one (each customer is their own tenant in `mtn-ghana`). Per CLAUDE.md §5.5.

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/auth/request_otp` | none | Request OTP delivery to MSISDN |
| `POST` | `/auth/verify_otp` | none | Verify OTP → session JWT |
| `GET`  | `/me/alerts` | session | Customer's alerts |
| `POST` | `/me/report` | session | Submit fraud report → `intel.events.v1` |
| `POST` | `/me/block` | session | Self-service block request → `intel.events.v1` |
| `GET`  | `/me/status` | session | MSISDN summary (open + recent alerts) |

`POST /auth/request_otp` returns 202 regardless of whether the MSISDN is provisioned, to avoid disclosing membership.

## Auth flow

1. Customer requests OTP — delivered out-of-band via SMS by the MTN OTP service (`HttpOtpAdapter`) or by an in-memory dev adapter (returns deterministic code `123456`).
2. Customer submits `(msisdn, code)` to `/auth/verify_otp`.
3. On success: HS256-signed session JWT (30 min TTL by default) carrying `msisdn` + `tenant_id`.
4. Subsequent `/me/*` requests use `Authorization: Bearer <session_token>`.

OTP backend swaps via env (DECISIONS.md D-005 — Phase 1 ships the stub; production cuts over to the security-team OTP service).

## Reports + blocks

Both write `IntelEventV1` to `intel.events.v1` with confidence ~0.5 — customer reports are valuable but not authoritative. The fraud team reviews high-volume report patterns and may promote to a Tier-1 block.

## Endpoints (operational)

| Path | Purpose |
|---|---|
| `GET /health/{live,ready}` | k8s probes |
| `GET /metrics` | Prometheus scrape |

## Runbook

[`docs/runbooks/api-customer.md`](../../docs/runbooks/api-customer.md)
