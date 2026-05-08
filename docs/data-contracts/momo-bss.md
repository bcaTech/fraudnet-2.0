# Data contract — MoMo BSS → ingest-momo

The MoMo Business Support System pushes events to `POST /webhooks/momo` whenever a transaction state transitions. ingest-momo translates these into the canonical `MoMoEventV1` (`packages/schemas/src/fraudnet/schemas/events.py`).

## Payload

```json
{
  "txn_id": "MTN-MOMO-2026-04-01-000001",
  "event_type": "P2P",
  "timestamp_ms": 1714492800000,
  "sender_wallet_id": "W:233241234567",
  "recipient_wallet_id": "W:233207654321",
  "sender_msisdn": "0241234567",
  "recipient_msisdn": "0207654321",
  "amount_minor": 5000,
  "currency": "GHS",
  "counterparty_kind": "wallet",
  "counterparty_account_hash": null,
  "is_reversal_of": null,
  "channel": "ussd",
  "bss_metadata": { "agent_id": "AG-12345" }
}
```

### Field rules

| Field | Required | Notes |
|---|---|---|
| `txn_id` | yes | Unique per transaction. Reversal `is_reversal_of` references the original `txn_id`. |
| `event_type` | yes | One of: `P2P`, `P2P_TRANSFER`, `DEPOSIT`, `CASH_IN`, `WITHDRAWAL`, `CASH_OUT`, `BILL`, `MERCHANT`, `BANK_TRANSFER`, `INTL_REMIT`, `REVERSAL`. Unknown values are rejected with HTTP 400 — the adapter never silently drops traffic. |
| `timestamp_ms` | yes | Event time, milliseconds since epoch UTC. Use the BSS event time, not delivery time. |
| `sender_wallet_id` / `recipient_wallet_id` | conditional | At least one must be present. Outbound chains use sender; cash-in events use recipient only. |
| `sender_msisdn` / `recipient_msisdn` | optional | E.164 or local Ghanaian (10-digit, leading 0) — both accepted, normalised to E.164. Malformed values return 400. |
| `amount_minor` | yes | Integer in the smallest currency unit (pesewas for GHS). |
| `currency` | yes | ISO-4217 three-letter code, any case. Normalised to uppercase. |
| `counterparty_kind` | yes | `wallet` \| `bank` \| `merchant` \| `agent` \| `external`. |
| `counterparty_account_hash` | optional | Hashed bank account; never plaintext. |
| `is_reversal_of` | optional | Required when `event_type=REVERSAL`. |
| `channel` | optional | `app` \| `ussd` \| `agent` \| `merchant_pos` \| `api`. |
| `bss_metadata` | optional | Free-form vendor extras. Carried through but ignored by the canonical layer. |

## Authentication

`X-MoMo-Signature: <hex(sha256(body, MOMO_WEBHOOK_SHARED_SECRET))>` — constant-time compared.

## Idempotency

ingest-momo derives `event_id = momo_<sha256(txn_id|event_type|timestamp_ms)[:24]>`. Duplicates within the 24-hour TTL are answered with `{"status":"duplicate"}` and 202; no Kafka write occurs.

## Versioning

This contract is `momo-bss/v1`. Breaking changes require a parallel `momo-bss/v2` endpoint and a dual-publish migration window agreed with the BSS vendor.
