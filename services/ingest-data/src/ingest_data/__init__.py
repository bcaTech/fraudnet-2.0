"""ingest-data — DNS resolver + IPDR ingestion.

Two adapters share one canonical event shape (`DataEventV1`) and one
Kafka topic (`data.events.v1`):

  - DNS push: queries from the network resolver. We capture
    queried domain + (optional) rdata. Subscriber attribution
    is best-effort: the resolver vendor maps source IP back to MSISDN
    where possible; events without an MSISDN are still emitted so
    stream-features can compute global domain reputation.
  - IPDR push: per-session IP detail records carrying subscriber MSISDN,
    destination domain (where DPI-derived) or destination IP, and
    upstream/downstream byte counts.

Body content (DNS qname, IPDR destination domain) is not classed as
PII per CLAUDE.md §5.1 — it is necessary for fraud-prevention purpose
and is gated by topic-level access control rather than per-event
purpose claims. MSISDN attribution IS PII; the audit-lib redacts it
in logs.
"""

__version__ = "0.1.0"
