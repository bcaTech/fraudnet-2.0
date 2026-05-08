"""ingest-sms — SMSC event ingestion.

Consumes SMSC pushes, normalises to canonical SmsEventV1, publishes to
Kafka topic `sms.events.v1`. Body content is gated on a regulatory
`purpose=fraud_prevention` claim per CLAUDE.md §5.1; without it we keep the
body_hash and template_hash but drop the plaintext.

URL extraction and template-hash clustering happen here so downstream
brain-content can act on hashes without re-scanning bodies. Both are cheap
deterministic transforms; the model lives in brain-content.
"""

__version__ = "0.1.0"
