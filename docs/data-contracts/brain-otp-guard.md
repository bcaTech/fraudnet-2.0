# Data contract — brain-otp-guard

## Inputs

### `voice.events.v1` (consumed)

Used to maintain the active inbound-call registry. Only `kind` and the
caller/callee MSISDNs are read.

| Field | Use |
|---|---|
| `kind` | `call_start` → registry insert; `call_end` → registry delete; others ignored |
| `caller` | Stored on the registry entry as `caller` for downstream evidence |
| `callee` | Registry key (the recipient's MSISDN). If null the event is dropped |
| `event_ts_ms` | Stored as `started_at_ms` |

### `sms.events.v1` (consumed)

Only `kind == "mt"` is processed. The detector reads `body` (when available),
`short_code`, `recipient`. `body_hash` / `template_hash` are not used here —
hash-based smishing already runs in `brain-content`.

## Output

### `fraud.signals.v1` — `signal_kind = "otp.during_call"`

Emitted only when an OTP-shaped MT SMS coincides with an active call to the
recipient. Severity is always `CRITICAL`.

```json
{
  "event_id": "sig_...",
  "event_ts_ms": 1714492800000,
  "ingest_ts_ms": 1714492800000,
  "source": "brain-otp-guard",
  "tenant_id": "mtn-ghana",
  "signal_kind": "otp.during_call",
  "subject": { "kind": "number", "id": "+233241234567" },
  "score": {
    "value": 0.97,
    "model_id": "otp-guard-heuristic",
    "model_version": "0.1.0",
    "computed_at_ms": 1714492800000
  },
  "severity": "critical",
  "evidence": {
    "caller": "+233207777777",
    "sms_sender": "+233231000000",
    "confidence": 0.97,
    "keyword_hits": 2,
    "short_code": "ECOBANK"
  },
  "suppression_key": "mtn-ghana:number:+233241234567:otp.during_call"
}
```

## Tier-1 actuator contract

Decisions translates `otp.during_call` to `otp.hold_and_alert`. The
`OtpHoldActuator` POSTs to `OTP_HOLD_URL`:

```json
{
  "msisdn": "+233241234567",
  "hold_duration_s": 60,
  "prompt": "otp_fraud_warning",
  "caller": "+233207777777",
  "decision_id": "dec_...",
  "policy_version": "2026-05-08-1"
}
```

The SMSC adapter is responsible for:

1. Delaying the next inbound MT SMS to `msisdn` for `hold_duration_s`.
2. Pushing a localised USSD prompt (text from `packages/i18n` key
   `otp_fraud_warning`) — the recipient confirms or cancels.
3. On confirm → release the SMS. On cancel → drop the SMS, write an audit
   event with the suppression key, no further alerting.
