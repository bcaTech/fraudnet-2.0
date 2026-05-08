"""Purpose-limitation primitives.

Per CLAUDE.md §7.2: every database connection sets a session GUC
`fraudnet.purpose`; PII-bearing tables enforce this via Postgres RLS. At the
application layer, a contextvar carries the current purpose so audit and
audit-aware DB clients can read it.

A service path that needs PII data must be wrapped in `with_purpose(...)`.
Code that reads from PII tables without an active purpose raises
`PurposeMissingError` at the audit-aware client boundary.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from fraudnet.schemas.errors import PurposeMissingError
from fraudnet.schemas.types import Purpose

_purpose_var: ContextVar[Purpose | None] = ContextVar("fraudnet_purpose", default=None)


class PurposeContext:
    """Holder for the active purpose. Use `with_purpose(...)` to set it."""

    def __init__(self, purpose: Purpose) -> None:
        self.purpose = purpose


def current_purpose() -> Purpose | None:
    return _purpose_var.get()


def require_purpose() -> Purpose:
    p = _purpose_var.get()
    if p is None:
        raise PurposeMissingError(
            "no purpose claim is active — wrap this access in with_purpose(...)",
        )
    return p


@contextmanager
def with_purpose(purpose: Purpose) -> Iterator[PurposeContext]:
    """Set the active purpose for the duration of the block.

    Stacking is allowed; the inner purpose wins until the block exits, then
    the outer purpose is restored. ContextVars carry through asyncio
    TaskGroups so this works in async code too.
    """
    token = _purpose_var.set(purpose)
    try:
        yield PurposeContext(purpose=purpose)
    finally:
        _purpose_var.reset(token)
