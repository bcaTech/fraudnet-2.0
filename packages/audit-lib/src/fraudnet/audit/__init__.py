"""Audit log primitives.

Every protected action across the platform writes here (CLAUDE.md §7.3):
  - Investigator viewing a customer profile.
  - Admin promoting a model.
  - Filing a takedown.
  - Exporting data for the regulator.

Service code calls `record(...)`. The implementation routes via Kafka topic
`audit.events.v1`, which the compliance service consumes into its append-only
Postgres archive with monthly Iceberg rotation. The audit log is the single
source of truth for regulator inquiries; do not bypass it.
"""

from fraudnet.audit.purpose import (
    PurposeContext,
    current_purpose,
    require_purpose,
    with_purpose,
)
from fraudnet.audit.record import (
    AuditScope,
    PurposeMissingError,
    configure_audit_writer,
    record,
)

__all__ = [
    "AuditScope",
    "PurposeContext",
    "PurposeMissingError",
    "configure_audit_writer",
    "current_purpose",
    "record",
    "require_purpose",
    "with_purpose",
]
