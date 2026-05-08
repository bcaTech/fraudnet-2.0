# Runbook — ingest-sms

## Purpose

SMSC push receiver. Body capture is purpose-gated (DPO + legal sign-off required).

## SLOs

| Metric | Target |
|---|---|
| Push accept p99 | < 50 ms |
| Kafka produce p99 | < 30 ms |
| Availability | 99.95% |

## Dashboards

- `rate(ingest_sms_received_total[1m])`
- `rate(ingest_sms_rejected_total[5m]) by (reason)`
- `rate(ingest_sms_body_captured_total[1m])` — should be ~0 unless body capture is authorised; spike means env flipped.

## Alert: body_captured rate non-zero in unauthorised env

This is a privacy concern. Verify `SMS_ALLOW_BODY_CAPTURE` env. If unintentionally enabled, redeploy with the flag off **immediately** and notify DPO. The captured bodies are now in `sms.events.v1` and need a redaction-replay job before downstream consumption.

## Alert: SMSC push parse_error rate spike

SMSC vendor pushed a payload format change. Inspect the DLQ via `tools/replay`; update the adapter or vendor shim accordingly.

## Vendor shim addition

For non-default SMSC vendors:
1. Add a vendor shim in `adapter.py` translating their format to `SmscPushEvent`.
2. Document in `docs/data-contracts/smsc-push.md` under "Vendor variants".
3. Set `SMS_SMSC_ID=<vendor>` for the deployment.

## Contacts

- Service team: @mtn-ghana/ingestion + @mtn-ghana/messaging
- DPO: @mtn-ghana/dpo (any privacy-related escalation)
