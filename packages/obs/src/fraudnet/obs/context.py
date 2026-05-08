"""Per-request context propagation.

`request_id` is set at the gateway and threaded through every log line, span,
and outbound call. `tenant_id` and `actor_id` are set after auth resolution.
Storage is contextvars-based so it Just Works with asyncio TaskGroups.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any
from uuid import uuid4

import structlog

# UUIDv7 would be nicer (time-ordered) — switch when uuid7 lands in stdlib or
# we pin a UUIDv7 lib globally. Until then, uuid4 is fine for log correlation.

_request_id: ContextVar[str | None] = ContextVar("fraudnet_request_id", default=None)
_tenant_id: ContextVar[str | None] = ContextVar("fraudnet_tenant_id", default=None)
_actor_id: ContextVar[str | None] = ContextVar("fraudnet_actor_id", default=None)


def new_request_id() -> str:
    return f"req_{uuid4().hex[:16]}"


def set_request_id(rid: str) -> None:
    _request_id.set(rid)


def get_request_id() -> str | None:
    return _request_id.get()


def bind_context(
    *,
    request_id: str | None = None,
    tenant_id: str | None = None,
    actor_id: str | None = None,
) -> None:
    if request_id is not None:
        _request_id.set(request_id)
    if tenant_id is not None:
        _tenant_id.set(tenant_id)
    if actor_id is not None:
        _actor_id.set(actor_id)


def clear_context() -> None:
    _request_id.set(None)
    _tenant_id.set(None)
    _actor_id.set(None)


def context_processor(_logger: object, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog processor: stamp every record with the current context."""
    rid = _request_id.get()
    tid = _tenant_id.get()
    aid = _actor_id.get()
    if rid is not None:
        event_dict.setdefault("request_id", rid)
    if tid is not None:
        event_dict.setdefault("tenant_id", tid)
    if aid is not None:
        event_dict.setdefault("actor_id", aid)
    return event_dict


def context_bind_for_structlog() -> structlog.BoundLogger:
    """Bind current context onto a fresh structlog logger.

    Used by service-level code that wants a logger with request fields already
    attached without going through the per-call processor.
    """
    base = structlog.get_logger()
    extras = {
        k: v
        for k, v in {
            "request_id": _request_id.get(),
            "tenant_id": _tenant_id.get(),
            "actor_id": _actor_id.get(),
        }.items()
        if v is not None
    }
    return base.bind(**extras)
