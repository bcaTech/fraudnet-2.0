# api-customer

Customer self-service API. MSISDN-OTP authentication; tenant-of-one (each customer is their own tenant in `mtn-ghana`). Per CLAUDE.md §5.5.

## Protection model

**Protection is on by default for every MTN subscriber.** The `api-customer` portal is an *enhancement layer* — not a gate on protection (DECISIONS.md D-008).

| Mode | Default? | Channels | Coverage |
|---|---|---|---|
| `passive` | Yes — every subscriber | SMS only | Spam-call/SMS warnings, OTP-fraud alert, URL-block notice, high-severity fraud alerts |
| `active` | Opt-in via portal | SMS + USSD + app push + self-service | Everything in `passive` plus Do-I-Know-You prompts, Ask-Me-First MoMo confirmation, MoMo limit reviews, SafeGuard auto-enrol |

The Tier-2 runner enforces this with `is_action_allowed(action, mode, severity)` before dispatching any actuator. Passive subscribers cannot be over-notified; active subscribers can opt back to passive at any time via the portal.

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/auth/request_otp` | none | Request OTP delivery to MSISDN |
| `POST` | `/auth/verify_otp` | none | Verify OTP → session JWT |
| `GET`  | `/me/alerts` | session | Customer's alerts |
| `POST` | `/me/report` | session | Submit fraud report → `intel.events.v1` |
| `POST` | `/me/block` | session | Self-service block request → `intel.events.v1` |
| `GET`  | `/me/status` | session | MSISDN summary (open + recent alerts) — localised banner |
| `GET`  | `/i18n/messages` | none | Bulk-dump of translated message templates for the negotiated locale |

`POST /auth/request_otp` returns 202 regardless of whether the MSISDN is provisioned, to avoid disclosing membership.

## Auth flow

1. Customer requests OTP — delivered out-of-band via SMS by the MTN OTP service (`HttpOtpAdapter`) or by an in-memory dev adapter (returns deterministic code `123456`).
2. Customer submits `(msisdn, code)` to `/auth/verify_otp`.
3. On success: HS256-signed session JWT (30 min TTL by default) carrying `msisdn` + `tenant_id`.
4. Subsequent `/me/*` requests use `Authorization: Bearer <session_token>`.

OTP backend swaps via env (DECISIONS.md D-005 — Phase 1 ships the stub; production cuts over to the security-team OTP service).

## i18n

All customer-facing surface respects `Accept-Language`. Supported locales: `en`, `tw`, `ga`, `ee`, `dag`, `ha`. Unknown / unsupported tags fall back to English. The localised string set lives in `packages/i18n/src/fraudnet/i18n/locales/<locale>.json`.

`GET /i18n/messages` returns the full template set for a single round-trip on the customer self-service web UI. `{variable}` tokens are returned unrendered — caller substitutes at delivery time.

## Reports + blocks

Both write `IntelEventV1` to `intel.events.v1` with confidence ~0.5 — customer reports are valuable but not authoritative. The fraud team reviews high-volume report patterns and may promote to a Tier-1 block.

## Endpoints (operational)

| Path | Purpose |
|---|---|
| `GET /health/{live,ready}` | k8s probes |
| `GET /metrics` | Prometheus scrape |

## Runbook

[`docs/runbooks/api-customer.md`](../../docs/runbooks/api-customer.md)
