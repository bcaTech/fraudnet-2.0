# ingest-voice

SS7/Diameter/IMS signaling event listener. Translates probe-vendor pushes into canonical `VoiceEventV1` and publishes to Kafka topic `voice.events.v1`.

On the **inline path** (CLAUDE.md §5.1) — probe → Kafka p99 budget is 30 ms.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/probe/voice` | Vendor probe push (HMAC `X-Probe-Signature`) |
| `GET`  | `/health/{live,ready}` | Probes |
| `GET`  | `/metrics` | Prometheus scrape |

See [`docs/data-contracts/voice-probe.md`](../../docs/data-contracts/voice-probe.md).

## Local dev

```bash
make infra-up && make kafka-topics-create
make dev SERVICE=ingest-voice
```

## Operational

- **Topic:** `voice.events.v1`. **DLQ:** `voice.events.dlq.v1`.
- **Partition key:** `caller` MSISDN — keeps a number's call chain in-order.
- **Idempotency:** Redis SET NX, 1h TTL. Cache fail-open.
- **Vendor neutrality:** swap vendor shims under `adapter.py` (Polystar / Subex / NetScout / EXFO) once RFI lands. The `GenericProbeEvent` is the contract.

## SLOs

| Metric | Target |
|---|---|
| Probe accept p99 | < 30 ms |
| Kafka produce p99 | < 30 ms |
| Availability | 99.99% — inline service |

## Runbook

[`docs/runbooks/ingest-voice.md`](../../docs/runbooks/ingest-voice.md)
