# Data contract — SMSC → ingest-sms

SMSC push to `POST /smsc/push`.

## Payload

```json
{
  "smsc_msg_id": "MSG-2026-04-01-00000001",
  "event_type": "MT",
  "timestamp_ms": 1714492800000,
  "sender": "0241234567",
  "recipient": "0207654321",
  "body": "Your verification code is 1234",
  "short_code": null,
  "smsc_id": "smsc-acc-01"
}
```

## Field rules

| Field | Required | Notes |
|---|---|---|
| `smsc_msg_id` | optional | Used as the dedup key directly when present. Otherwise derived. |
| `event_type` | yes | One of `MT`, `MO`, `MT_DR` (also `MOBILE_TERMINATED`, `MOBILE_ORIGINATED`, `DELIVERY_RECEIPT`). Case-insensitive. |
| `timestamp_ms` | yes | Event time UTC ms. |
| `sender` / `recipient` | yes | E.164 or local Ghanaian. Normalised to E.164. |
| `body` | optional | Plaintext. **Captured into the canonical event only when `SMS_ALLOW_BODY_CAPTURE=true`** (purpose-gated). `body_hash` and `template_hash` are derived regardless. |
| `short_code` | optional | Sender short code if applicable. |
| `smsc_id` | optional | Falls back to the service-level `SMS_SMSC_ID` env. |

## Authentication

`X-SMSC-Signature: <hex(sha256(body, SMS_WEBHOOK_SHARED_SECRET))>`. Constant-time compared.

## Idempotency

- `event_id = sms_<smsc_msg_id[:32]>` if supplied.
- Else `sms_<sha256(sender|recipient|event_type|timestamp_ms)[:24]>`.

Default 2-hour TTL.

## Privacy

- Body content is dropped at the adapter when capture is disabled. Hash and template hash are always derived from the body before the drop.
- Logs never carry raw body text. `obs.scrub_text()` is the safety net.
