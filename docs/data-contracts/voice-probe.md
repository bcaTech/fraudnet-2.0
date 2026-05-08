# Data contract — Voice probe → ingest-voice

Generic probe push to `POST /probe/voice`. Vendor-neutral; specific shims (Polystar, Subex, NetScout, EXFO) pre-translate to this shape.

## Payload

```json
{
  "cdr_id": "CDR-2026-04-01-0000001",
  "event_type": "CALL_START",
  "timestamp_ms": 1714492800000,
  "caller": "0241234567",
  "callee": "0207654321",
  "imsi": "620010123456789",
  "imei": "359123456789012",
  "duration_s": 0,
  "cell_id": "12345",
  "location_area_code": "ACC-LA01",
  "network": "VoLTE",
  "vendor_meta": { "trunk_id": "T-42" }
}
```

## Field rules

| Field | Required | Notes |
|---|---|---|
| `cdr_id` | optional | If supplied, used directly as the dedup key. Otherwise derived from `(caller, callee, event_type, timestamp_ms)`. |
| `event_type` | yes | One of: `CALL_START`, `CALL_END`, `REGISTRATION` (also `REGISTER`), `HANDOVER` (also `HANDOFF`). Case-insensitive. Unknown → 400. |
| `timestamp_ms` | yes | Event time UTC ms. Use the probe-event time, not delivery time. |
| `caller` | yes | E.164 or local Ghanaian (10-digit); normalised to E.164. Junk → 400. |
| `callee` | optional | Absent for `REGISTRATION` / `HANDOVER`. Same MSISDN rules. |
| `imsi` | optional | 14–15 digits. |
| `imei` | optional | 14–17 digits. |
| `duration_s` | optional | Required for `CALL_END`; the probe vendor enforces this upstream. |
| `cell_id` / `location_area_code` | optional | Used by stream-features for geographic motion entropy. |
| `network` | optional | `2G` \| `3G` \| `4G` \| `5G` \| `VoLTE` \| `VoWiFi`. |
| `vendor_meta` | optional | Free-form vendor extras; passed through to canonical event. |

## Authentication

`X-Probe-Signature: <hex(sha256(body, VOICE_WEBHOOK_SHARED_SECRET))>`. Constant-time compared.

## Idempotency

Derived `event_id`:
- If `cdr_id` is present: `voice_<cdr_id[:32]>`.
- Else: `voice_<sha256(caller|callee|event_type|timestamp_ms)[:24]>`.

Default 1-hour TTL. CDR redelivery on probe restart is the canonical use case.

## Versioning

`voice-probe/v1`. Vendor-specific extensions land as additive optional fields with defaults.
