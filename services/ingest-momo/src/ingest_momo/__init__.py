"""ingest-momo — MoMo event ingestion.

Translates MoMo BSS events into the canonical MoMoEventV1 wire format and
publishes to Kafka topic `momo.events.v1`. This is the most stable of the
ingest services and extends the existing FraudNet 1.0 MoMo integration
(CLAUDE.md §5.1).

Inputs: HTTPS webhook from MoMo BSS (push model), per-event idempotency key.
Outputs: momo.events.v1 with partition key = sender_wallet_id when present,
else recipient_wallet_id. This guarantees event ordering within a wallet.
"""

__version__ = "0.1.0"
