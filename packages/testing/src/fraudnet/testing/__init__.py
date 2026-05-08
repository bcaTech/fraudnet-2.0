"""Test fixtures, factories, and integration helpers.

Factories use realistic Ghanaian data per CLAUDE.md §10.5. Integration tests
spin up real services (Kafka, Postgres, Memgraph) via Testcontainers — never
mocks (CLAUDE.md §10.5 again).
"""

from fraudnet.testing.factories import (
    fake_imei,
    fake_msisdn,
    fake_wallet_id,
    make_audit_event,
    make_momo_event,
    make_sms_event,
    make_voice_event,
)
from fraudnet.testing.kafka import EphemeralKafka

__all__ = [
    "EphemeralKafka",
    "fake_imei",
    "fake_msisdn",
    "fake_wallet_id",
    "make_audit_event",
    "make_momo_event",
    "make_sms_event",
    "make_voice_event",
]
