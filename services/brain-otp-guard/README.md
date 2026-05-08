# brain-otp-guard

OTP fraud interception. Correlates active inbound calls with incoming OTP-bearing
SMS to catch the canonical vishing-then-OTP-extraction pattern: a scammer keeps
the victim on a phone call, induces them to start a transaction at their bank,
and waits for the OTP SMS to land while the victim reads it out.

## What it does

Two consumers, one rule:

1. **voice.events.v1** → maintain an *active inbound call* registry keyed on
   the callee MSISDN (Redis hash, 15 min TTL).
2. **sms.events.v1** (MT only) → for each MT SMS, run the OTP detector
   (short-code + keywords + 4–8 digit code). If the SMS looks like an OTP
   *and* the recipient currently has an active call → emit
   `otp.during_call` to `fraud.signals.v1` with severity `CRITICAL`.

The decisions service maps `otp.during_call` → Tier-1 action
`otp.hold_and_alert`, dispatched by the `OtpHoldActuator` in
`action-tier1`. The actuator holds the OTP at the SMSC and pushes a USSD
warning to the recipient.

## Detection rules

| Signal | Trigger |
|---|---|
| Bank short code | sender short_code ∈ configured bank list |
| Keyword + code | body contains an OTP keyword AND a 4–8 digit code |
| Multiple keywords | body contains ≥2 OTP keywords (lower confidence) |

A signal fires *only when* the SMS-side detection is positive **and** the
recipient has an active inbound call. This conjunction is what gives the
detector its precision.

## Suppression

Per recipient MSISDN, default 5-minute window. Same MSISDN getting a
follow-up OTP within the window is dropped (the action is already in
flight).

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/health/{live,ready}` | k8s probes |
| `GET`  | `/metrics` | Prometheus scrape |

## Operational

- **Inputs:** `voice.events.v1`, `sms.events.v1`
- **Output:** `fraud.signals.v1` keyed by recipient msisdn
- **Suppression key:** `<tenant>:number:<recipient>:otp.during_call`
- **State:** Redis (`BRAIN_OTP_REDIS_URL`, default DB 3)
- **Bank short codes:** `BRAIN_OTP_BANK_SHORT_CODES` (CSV)

## Configuration

| Env var | Default | Notes |
|---|---|---|
| `BRAIN_OTP_REDIS_URL` | `redis://localhost:6379/3` | Active-call registry + suppression |
| `BRAIN_OTP_ACTIVE_CALL_TTL_S` | `900` | TTL on missed CALL_END |
| `BRAIN_OTP_SUPPRESSION_S` | `300` | Per-recipient suppression window |
| `BRAIN_OTP_BANK_SHORT_CODES` | `MTN,ECOBANK,...` | CSV of trusted bank/fintech sender short codes |

## Runbook

[`docs/runbooks/brain-otp-guard.md`](../../docs/runbooks/brain-otp-guard.md)
