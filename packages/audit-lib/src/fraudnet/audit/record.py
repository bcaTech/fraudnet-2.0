"""record() — the only way to write an audit event.

Service code calls:

    await record(
        action="alerts.claim",
        resource_kind="alert",
        resource_id=str(alert_id),
        metadata={"severity": "high"},
    )

The current purpose, request_id, tenant_id, and actor_id are pulled from
contextvars (set at the gateway / auth boundary). If no purpose is active, the
call raises PurposeMissingError — the action is not auditable, and we fail
closed.

The writer is pluggable so that:
  - Production routes to Kafka topic `audit.events.v1`.
  - Tests route to an in-memory list for assertion.
  - Local dev can route to stdout when Kafka is not available.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from time import time
from typing import Any
from uuid import uuid4

from fraudnet.audit.purpose import require_purpose
from fraudnet.obs import bind_context as _bind  # noqa: F401  — re-export hint
from fraudnet.obs import get_logger, get_request_id, redact_mapping
from fraudnet.schemas.audit import AuditEventV1
from fraudnet.schemas.errors import PurposeMissingError

_log = get_logger("fraudnet.audit")


@dataclass(frozen=True)
class AuditScope:
    actor_id: str | None = None
    actor_kind: str = "service"  # user | service | system
    tenant_id: str = "mtn-ghana"
    service: str = "unknown"
    extra: dict[str, Any] = field(default_factory=dict)


# Module-level scope set at service startup. Per-call overrides are passed via
# kwargs.
_scope: AuditScope = AuditScope()


def set_scope(scope: AuditScope) -> None:
    """Set the module-level scope (called once at service startup)."""
    global _scope
    _scope = scope


class AuditWriter(ABC):
    @abstractmethod
    async def write(self, event: AuditEventV1) -> None: ...


class _StdoutWriter(AuditWriter):
    """Default writer for dev / tests when no Kafka producer is wired up."""

    async def write(self, event: AuditEventV1) -> None:
        _log.info("audit", **redact_mapping(event.model_dump(mode="json")))


_writer: AuditWriter = _StdoutWriter()


def configure_audit_writer(writer: AuditWriter) -> None:
    """Replace the active writer. Call once at service startup."""
    global _writer
    _writer = writer


async def record(
    *,
    action: str,
    resource_kind: str,
    resource_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    actor_id: str | None = None,
    actor_kind: str | None = None,
    tenant_id: str | None = None,
) -> None:
    """Write an auditable action.

    Raises:
        PurposeMissingError: no purpose is active in this context. The call
            is rejected — audit events without a purpose are useless to the
            regulator and dangerous to keep.
    """
    purpose = require_purpose()  # raises PurposeMissingError if unset
    event = AuditEventV1(
        event_id=f"aud_{uuid4().hex[:24]}",
        event_ts_ms=int(time() * 1000),
        actor_id=None if actor_id is None else _maybe_uuid(actor_id),
        actor_kind=actor_kind or _scope.actor_kind,
        action=action,
        resource_kind=resource_kind,
        resource_id=resource_id,
        purpose=purpose,
        request_id=get_request_id(),
        tenant_id=tenant_id or _scope.tenant_id,
        metadata=_safe_metadata(metadata or {}),
    )
    await _writer.write(event)


def _maybe_uuid(s: str) -> Any:
    from uuid import UUID

    try:
        return UUID(s)
    except (TypeError, ValueError):
        return None


def _safe_metadata(md: dict[str, Any]) -> dict[str, Any]:
    """Audit metadata is dotted into Iceberg over time; keep it primitive."""
    out: dict[str, Any] = {}
    for k, v in md.items():
        if isinstance(v, (str, int, float, bool)):
            out[k] = v
        else:
            out[k] = str(v)
    return out


__all__ = [
    "AuditScope",
    "AuditWriter",
    "PurposeMissingError",
    "configure_audit_writer",
    "record",
    "set_scope",
]
