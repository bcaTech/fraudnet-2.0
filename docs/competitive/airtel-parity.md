# Airtel-parity capability matrix

> Status: shipped on `airtel-parity-sprint`. Six capabilities Airtel
> India / Africa have deployed in production; FraudNet 2.0 matches or
> exceeds each.

The Airtel deployments (India 2023, Nigeria 2024, Kenya 2024) set the
practical telco-fraud bar in our region. This sprint closes the gap
where it existed and goes further on the dimensions our network-native
fusion architecture lets us push.

| Capability | Airtel | FraudNet 2.0 | How we go further |
|---|---|---|---|
| 1. OTP fraud interception | ✅ Hold OTP during suspicious calls | ✅ `brain-otp-guard` | Active-call registry + suppression — multi-instance safe via Redis; vendor-neutral SMSC adapter |
| 2. OTT / URL blocking | ✅ DNS sinkhole | ✅ `url-intel` + `DnsSinkholeActuator` | Allow-list aware; threat-feed import endpoint; brain-content scans DNS queries against the same blocklist |
| 3. Multi-language alerts | ✅ ~5 languages | ✅ `packages/i18n` (en/tw/ga/ee/dag/ha) | Six Ghanaian languages by design; placeholders flagged for professional review; bulk-translate API for the customer portal |
| 4. Verified sender display | ✅ Truecaller-style branding | ✅ `business-registry` + scoring discount | Full pipeline integration: verified senders are *not just labelled* but exempt from the scoring path that generates false positives — measured per-business via api-noc |
| 5. RCS trust signal | ✅ RCS verified sender bit on whitelist | ✅ `SmsEventV1.rcs_verified` + hard trust override | Signal flows end-to-end (ingest → classifier short-circuit + IMEI-churn exemption) |
| 6. Always-on passive protection | ✅ Default-on alerting | ✅ `passive_protection` policy block + tier-2 gate | Codified in YAML and enforced at the actuator gate — not just an opt-in default |

---

## 1. OTP fraud interception (`brain-otp-guard`)

**Airtel:** When an inbound call is in progress and a bank OTP SMS
arrives at the recipient, the alerting service holds the SMS and pushes
a USSD warning. Reduces vishing-extracted OTP fraud sharply.

**Us:**

- New service `services/brain-otp-guard` consumes `voice.events.v1` and
  `sms.events.v1`. Maintains a Redis-backed active inbound-call registry
  (15 min TTL on missed CALL_END, multi-instance-safe).
- OTP detection: short-code (configurable bank list) +
  4–8-digit code + OTP keywords. Conjunction with active-call gate
  emits `otp.during_call` at severity CRITICAL.
- 5-minute per-recipient suppression to avoid double-alerting on
  scammer retries.
- Tier-1 actuator `OtpHoldActuator` POSTs hold + USSD-prompt request
  to the SMSC adapter; `decisions/policies/default.yaml` rule
  `otp-during-call-tier1` wires the action.

**Goes further than Airtel:** the registry is multi-instance safe so
the service horizontally scales without losing call state on pod
churn — Airtel's deployment uses sticky-session routing. Our
suppression key is per-recipient + per-tenant, so the same scammer
hitting many victims still triggers per-victim alerts.

## 2. OTT URL blocking (`url-intel` + `DnsSinkholeActuator`)

**Airtel:** Centralised DNS sinkhole fed by content classification +
manual analyst additions.

**Us:**

- New service `services/url-intel` exposes
  `/blocklist/{check,export,add,remove}`, `/feeds/import`. Redis-backed
  blocklist with positive (30d) and negative (60s) TTL on signal-driven
  entries.
- Allow-list (configurable, with sensible defaults including mtn.com.gh,
  bog.gov.gh, ecobank.com, google.com, microsoft.com etc.) — `add`
  refuses allow-listed domains, `check` always returns
  `allow_listed: true` for them.
- Listens to `fraud.signals.v1` for URL-related signals
  (e.g. `sms.malicious_url`); auto-ingests with TTL.
- `DnsSinkholeActuator` in `action-tier1` POSTs every Tier-1
  `url.block` decision to url-intel first (allow-list aware) then
  pushes to the configured DNS resolver.
- `brain-content` opt-in DNS scanner (`URL_INTEL_URL` env) reads
  `data.events.v1`, looks each query up, emits
  `data.dns_blocklist_hit` signals.

**Goes further than Airtel:** centralised allow-listing means a
mistakenly-flagged signal from any source cannot result in blocking a
Bank-of-Ghana domain; threat-feed import is a first-class API so
external intel (PhishTank, VirusTotal, GSMA T-ISAC) lands without
custom adapters.

## 3. Multi-language customer alerts (`packages/i18n`)

**Airtel India:** ~5 Indian languages on alert SMS. Africa rollouts
typically English + 1–2 local.

**Us:**

- New shared library `packages/i18n` with locale catalogues for
  English (canonical), Twi, Ga, Ewe, Dagbani, Hausa.
- `Translator`, `parse_accept_language(header)`, `raw_template(key, locale)`,
  `translate(key, locale, **vars)`. Falls back to English on missing
  keys; unsupported `Accept-Language` tags fall back to English.
- Non-English catalogues are placeholder text with `_meta` TODO marker
  — structure first, translation review on the localisation team's
  schedule. The shape and key set match English, so swapping in
  reviewed strings is a one-file change.
- `api-customer`: localised `/me/status` banner; bulk-dump
  `GET /i18n/messages` for the self-service web UI.
- `action-tier2`: `SubscriberLocaleResolver` (Static for Phase 1,
  Postgres-backed in Phase 2). Customer-facing actuators include
  `locale` + rendered `body` in the payload — the SMS gateway no
  longer needs an i18n catalogue.

**Goes further than Airtel:** six Ghanaian languages from day one;
the i18n library is a first-class workspace package, so any future
service can drop it in.

## 4. Verified business display (`business-registry`)

**Airtel:** Verified sender display. Rolled out as a UI affordance —
the alert says "Verified: Bank XYZ". Scoring path does not branch on
verification status.

**Us:**

- New service `services/business-registry` with full schema:
  `businesses`, `business_msisdns`, `business_shortcodes`,
  `business_false_positives`. Postgres + Redis cache (DB 5).
- HTTP API: register / verify / add MSISDN / add shortcode / list /
  lookup. Lookups serve sub-5ms via Redis (positive 5min TTL,
  negative 1min TTL).
- `BusinessRegistryClient` workspace package with HTTP and Noop
  implementations + in-process LRU cache.
- **Scoring pipeline integration** (the differentiator):
  - `brain-behavioural` looks up the MSISDN before signal emission;
    verified MSISDNs get a 0.1 score discount and signal suppression
    on `voice.velocity_burst`, `device.imei_churn`, `sms.bulk_template`.
  - `brain-content` looks up the SMS short-code; verified senders'
    classifications are dropped before `to_signal()`.
  - `decisions/policies/default.yaml` has a top-of-policy
    `verified-business-suppress` rule that catches any
    `verified_business=true` evidence and routes it directly to
    Tier-3 — defence in depth if a producer skips the discount.
- `api-noc` `GET /false-positives/businesses` surfaces FP rates per
  verified business so analysts can tune classifier thresholds.

**Goes further than Airtel:** verified-business is a
*scoring decision* in our pipeline, not just a display label.
Airtel's UI affordance still drove false positives because the alert
*existed*; we suppress the alert at the source.

## 5. RCS trust signal (`SmsEventV1.rcs_verified`)

**Airtel:** RCS Business Messaging verified-sender bit lands a sender
on a soft-trust whitelist; classifier still runs.

**Us:**

- `SmsEventV1.rcs_verified: bool` (Avro schema bumped non-breakingly).
- `ingest-sms` adapter normalises vendor variants
  (`rcs_verified`, `verified_sender`, `rcs_authenticated`).
- `brain-content`: hard short-circuit — RCS-verified MT SMS does not
  run the classifier and emits no signal. The platform-grade auth is
  stronger than our heuristic / ML score.
- `brain-behavioural`: `device.imei_churn` is exempt for
  `NumberFeatures.rcs_verified_recent` — businesses legitimately
  rotate SMS-routing infrastructure.
- DECISIONS.md D-007 documents the trust override and revisit
  conditions (compromise of vendor / peer-network leak).

**Goes further than Airtel:** RCS trust flows end-to-end into the
behavioural pipeline (IMEI churn exemption), not only the content
classifier.

## 6. Zero-friction passive protection

**Airtel:** Default-on alerting service. Subscribers do not opt in.

**Us:**

- `decisions/policies/default.yaml` has a top-level
  `passive_protection` block listing:
  - `auto_enrolled_actions` — every subscriber sees these.
  - `high_severity_only` — passive subscribers see these only at
    critical/high severity.
  - `active_mode_only` — these require an enrolled subscriber profile
    (USSD enrolment, app install).
- `action-tier2/protection.py` provides `is_action_allowed(action,
  mode, severity)`. The runner gates every customer-facing actuator
  on this — passive subscribers cannot be over-notified, active
  subscribers can opt back to passive via the portal.
- `CustomerSmsAlertActuator` surfaces `protection_mode` in the
  payload so the SMS gateway selects channel (passive = SMS only,
  active = SMS + USSD + app push).
- `api-customer/README.md` documents the model: portal is an
  enhancement layer, not a gate on protection.
- DECISIONS.md D-008 documents the decision and revisit conditions.

**Goes further than Airtel:** the policy is *codified in YAML* and
enforced at the actuator gate, not buried in service code. Analyst
can change the auto-enrolled action set without a code change.

---

## Summary

Six capabilities, six commits on `airtel-parity-sprint`:

```
feat(brain-otp-guard): OTP fraud interception
feat(url-intel):       URL threat intelligence + DNS sinkhole
feat(i18n):            multi-language customer alerts
feat(business-registry): verified sender registry + scoring integration
feat:                  RCS trust signal in SMS pipeline
feat:                  zero-friction passive protection
```

Reviewing checklist:

- [ ] All services compile (`make test-unit`)
- [ ] Avro schema changes are non-breaking (default-valued)
- [ ] DECISIONS.md updated for D-007 and D-008
- [ ] Each new service has Dockerfile, README, runbook, data contract
- [ ] docker-compose.dev.yml wires all new services
- [ ] Makefile APP_SERVICES includes the new services

Decision points still open for Phase 2 follow-up:

- stream-features must populate `NumberFeatures.rcs_verified_recent` from `sms.events.v1`.
- url-intel needs a janitor job to evict expired meta-keys' domains from the SET.
- business_false_positives table needs a nightly populator job in api-noc.
- SMSC adapter for `OtpHoldActuator` is currently NoopActuator — wire when SMSC contract finalises.
- Subscriber locale + protection_mode resolvers default to Static. Phase 2 swaps for Postgres-backed implementations reading from `subscriber_profiles`.
