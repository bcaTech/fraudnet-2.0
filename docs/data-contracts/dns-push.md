# Data contract — DNS resolver → ingest-data

Resolver push to `POST /dns/push`.

## Payload

```json
{
  "query_id": "Q-2026-04-01-00000001",
  "event_type": "QUERY",
  "timestamp_ms": 1714492800000,
  "msisdn": "0241234567",
  "qname": "login-mtn-momo.example.com",
  "qtype": "A",
  "rdata": null,
  "rcode": null,
  "resolver_id": "res-acc-01"
}
```

For response events (`event_type=RESPONSE`), `rdata` carries the resolved value (IP for A/AAAA, FQDN for CNAME) and `rcode` is set (`NOERROR`, `NXDOMAIN`, …).

## Field rules

| Field | Required | Notes |
|---|---|---|
| `query_id` | optional | Used as the dedup key directly when present. Otherwise derived from `(msisdn, qname, event_type, timestamp_ms)`. |
| `event_type` | yes | One of `QUERY`, `RESPONSE` (also `Q`, `R`, `DNS_QUERY`, `DNS_RESPONSE`). Case-insensitive. |
| `timestamp_ms` | yes | Event time UTC ms. |
| `msisdn` | optional | Resolver-side IP→MSISDN attribution. Absent for unattributed traffic; the event is still emitted (domain reputation aggregations still benefit). |
| `qname` | yes | Lowercased, trailing-dot-stripped, IDN→A-label normalised at the adapter. |
| `qtype` | optional | DNS record type. |
| `rdata` | optional | Resolved IP or CNAME on response events; canonicalised. |
| `rcode` | optional | DNS response code. |
| `resolver_id` | optional | Falls back to service-level `DATA_DNS_RESOLVER_ID` env. |

## Authentication

`X-DNS-Signature: <hex(sha256(body, DATA_DNS_WEBHOOK_SHARED_SECRET))>`. Constant-time compared.

## Idempotency

- `event_id = dns_<query_id[:32]>` if supplied.
- Else `event_id = dns_<sha256(msisdn|canonical_qname|event_type|timestamp_ms)[:24]>`.

Default 1-hour TTL.

## Privacy

- DNS qnames are not classed as message-content PII (CLAUDE.md §5.1) but reveal browsing intent and are subject to the regulatory purpose-limitation envelope.
- Logs never carry MSISDN plaintext; `obs.redact()` is used wherever MSISDN can leak.
