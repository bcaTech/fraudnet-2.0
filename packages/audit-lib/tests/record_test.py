"""Audit record() — purpose enforcement and writer plumbing."""

from __future__ import annotations

import pytest

from fraudnet.audit import (
    AuditScope,
    PurposeMissingError,
    configure_audit_writer,
    record,
    with_purpose,
)
from fraudnet.audit.record import AuditWriter, set_scope
from fraudnet.schemas.audit import AuditEventV1
from fraudnet.schemas.types import Purpose


class _MemoryWriter(AuditWriter):
    def __init__(self) -> None:
        self.events: list[AuditEventV1] = []

    async def write(self, event: AuditEventV1) -> None:
        self.events.append(event)


@pytest.fixture
def writer() -> _MemoryWriter:
    w = _MemoryWriter()
    configure_audit_writer(w)
    set_scope(AuditScope(actor_kind="service", tenant_id="mtn-ghana", service="test"))
    return w


async def test_record_requires_active_purpose(writer: _MemoryWriter) -> None:
    with pytest.raises(PurposeMissingError):
        await record(action="alerts.claim", resource_kind="alert", resource_id="a1")
    assert writer.events == []


async def test_record_with_purpose(writer: _MemoryWriter) -> None:
    with with_purpose(Purpose.FRAUD_PREVENTION):
        await record(
            action="alerts.claim",
            resource_kind="alert",
            resource_id="a1",
            metadata={"severity": "high"},
        )

    assert len(writer.events) == 1
    ev = writer.events[0]
    assert ev.action == "alerts.claim"
    assert ev.resource_kind == "alert"
    assert ev.purpose == Purpose.FRAUD_PREVENTION
    assert ev.metadata["severity"] == "high"
    assert ev.tenant_id == "mtn-ghana"


async def test_metadata_is_coerced_to_primitives(writer: _MemoryWriter) -> None:
    class Funky:
        def __str__(self) -> str:
            return "funky"

    with with_purpose(Purpose.FRAUD_PREVENTION):
        await record(
            action="x.y",
            resource_kind="z",
            metadata={"obj": Funky(), "n": 42},
        )

    ev = writer.events[0]
    assert ev.metadata == {"obj": "funky", "n": 42}


async def test_purpose_stacks(writer: _MemoryWriter) -> None:
    with with_purpose(Purpose.FRAUD_PREVENTION):
        with with_purpose(Purpose.AUDIT):
            await record(action="x", resource_kind="y")
        await record(action="x", resource_kind="y")

    assert writer.events[0].purpose == Purpose.AUDIT
    assert writer.events[1].purpose == Purpose.FRAUD_PREVENTION
