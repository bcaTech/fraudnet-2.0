# Data contract — IPDR collector → ingest-data

Collector push to `POST /ipdr/push`.

## Payload

```json
{
  "session_id": "IPDR-2026-04-01-00000001",
  "timestamp_ms": 1714492800000,
  "msisdn": "0241234567",
  "dst_domain": "cdn.example.com",
  "dst_ip": "203.0.113.42",
  "bytes_up": 1342,
  "bytes_down": 88210,
  "duration_s": 47,
  "collector_id": "ipdr-acc-01"
}
```

At least one of `dst_domain` and `dst_ip` MUST be present; sessions with neither are dropped at the adapter (`adapter_rejected`).

## Field rules

| Field | Required | Notes |
|---|---|---|
| `session_id` | optional | Used as the dedup key directly when present. Otherwise derived. |
| `timestamp_ms` | yes | Session-end (or session-tick) UTC ms. |
| `msisdn` | yes | Subscriber attribution. IPDR is always attributed. |
| `dst_domain` | optional | DPI-derived host. Lowercased + IDN→A-label normalised at the adapter. |
| `dst_ip` | optional | Validated IPv4/IPv6; canonicalised. |
| `bytes_up` / `bytes_down` | yes | Bytes upstream / downstream within the session window. |
| `duration_s` | optional | Session duration. |
| `collector_id` | optional | Falls back to service-level `DATA_IPDR_COLLECTOR_ID` env. |

## Authentication

`X-IPDR-Signature: <hex(sha256(body, DATA_IPDR_WEBHOOK_SHARED_SECRET))>`. Constant-time compared.

## Idempotency

- `event_id = ipdr_<session_id[:32]>` if supplied.
- Else `event_id = ipdr_<sha256(msisdn|dst_domain_or_ip|timestamp_ms)[:24]>`.

Default 1-hour TTL.

## Privacy

- MSISDN is PII; logs and metrics never carry it.
- Per-session bytes are aggregated downstream; raw IPDR rows are retained for 7 days in `data.events.v1` per CLAUDE.md §6.3.
