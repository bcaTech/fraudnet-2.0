"""ingest-voice — SS7/Diameter/IMS CDR ingestion.

Translates probe-vendor signaling events into the canonical VoiceEventV1 wire
format and publishes to Kafka topic `voice.events.v1`. This service is on the
inline path (CLAUDE.md §5.1): probe → Kafka latency budget is 30 ms p99.

Vendor-neutral: a `VoiceProbeAdapter` interface lets the integration layer
swap between Polystar / Subex / NetScout / EXFO once the RFI selection lands.
"""

__version__ = "0.1.0"
