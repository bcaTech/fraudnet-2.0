# Runbook — brain-otp-guard

## Purpose

OTP fraud interception. Correlates active inbound voice calls (`voice.events.v1`)
with incoming OTP-bearing SMS (`sms.events.v1`) to catch the canonical
vishing-then-OTP-extraction pattern. Emits `otp.during_call` to
`fraud.signals.v1` at severity CRITICAL.

## SLOs

| Metric | Target |
|---|---|
| Active-call registry write (Redis HSET + EXPIRE) | < 2 ms p99 |
| OTP detection per SMS (regex + lookup) | < 1 ms p99 |
| Signal emission p95 (CALL_START → fraud.signals.v1) | < 2 s |
| Suppression Redis SETNX p99 | < 2 ms |

## Dashboards

- `rate(brain_otp_guard_voice_events_total[1m]) by (kind)`
- `rate(brain_otp_guard_sms_events_total[1m]) by (is_otp, active_call, fired)`
- `rate(brain_otp_guard_signals_total[5m]) by (severity)`
- `rate(fraudnet_kafka_messages_dlq_total{topic=~"voice.events.v1|sms.events.v1"}[5m])`

## Alert: signals fired but no Tier-1 action observed

Signals are flowing on `fraud.signals.v1` but `action_tier1_invocations_total{action="otp.hold_and_alert"}` is flat.

1. Verify the policy rule `otp-during-call-tier1` is loaded:
   `curl http://decisions:8401/policy/dump | jq '.rules[] | select(.id=="otp-during-call-tier1")'`
2. Verify `OtpHoldActuator` is registered:
   `curl http://action-tier1:8201/registry`
3. Check the `OTP_HOLD_URL` env on action-tier1; if empty, the registry binds
   the NoopActuator (logs only, returns dry_run). For production wiring set
   `OTP_HOLD_URL` to the SMSC adapter.

## Alert: false-positive rate climbs (`fired=true` ratio out of expected band)

False positives most often come from a misconfigured `BRAIN_OTP_BANK_SHORT_CODES`
list — short codes that are not real bank senders. Reduce the list and
restart. Detector regression tests live in `services/brain-otp-guard/tests/detector_test.py`.

## Alert: CALL_START high, CALL_END flat (registry leak)

Vendor flap — call-end events not flowing. Active calls auto-expire on
`BRAIN_OTP_ACTIVE_CALL_TTL_S` (default 15 min). If the rate of leaks is
sustained, lower the TTL temporarily; long-term, work with the probe
vendor to restore CALL_END coverage.

## Configuration

| Env var | Default | Notes |
|---|---|---|
| `BRAIN_OTP_REDIS_URL` | `redis://localhost:6379/3` | DB 3 by convention |
| `BRAIN_OTP_ACTIVE_CALL_TTL_S` | `900` | 15 min |
| `BRAIN_OTP_SUPPRESSION_S` | `300` | 5 min per recipient |
| `BRAIN_OTP_BANK_SHORT_CODES` | `MTN,ECOBANK,...` | CSV — review quarterly |

## SMSC integration (Phase 1 stub → Phase 2 production)

The `OtpHoldActuator` posts to `OTP_HOLD_URL`. In Phase 1 this is unset,
so the NoopActuator runs (logs the intent, no real SMSC mutation). Phase 2
wires the SMSC HTTP adapter:

```
POST {OTP_HOLD_URL}
{
  "msisdn": "+233241234567",
  "hold_duration_s": 60,
  "prompt": "otp_fraud_warning",
  "caller": "+233207777777",
  "decision_id": "dec_...",
  "policy_version": "..."
}
```

The SMSC adapter is responsible for:
1. Holding the next inbound MT SMS to `msisdn` for `hold_duration_s` (or until cancelled).
2. Pushing a localised USSD prompt to the recipient (text from `packages/i18n` `otp_fraud_warning`).

## Contacts

- Service team: @mtn-ghana/fraud-eng + @mtn-ghana/messaging
- SMSC integration: @mtn-ghana/messaging-platform
