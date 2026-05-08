"""Audit event — separate from the domain events because it is sensitive.

`audit.events.v1` is the single source of truth for regulator inquiries
(CLAUDE.md §7.3). Every protected action across the platform writes here.
"""

from __future__ import annotations

from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from fraudnet.schemas.types import Purpose


class AuditEventV1(BaseModel):
    """An auditable action.

    Producers do not write this directly; they use `fraudnet.audit.record()`,
    which fills in the actor/request/purpose context from the request scope.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    topic: ClassVar[str] = "audit.events.v1"

    event_id: str = Field(min_length=8, max_length=64)
    event_ts_ms: int = Field(ge=0)
    actor_id: UUID | None = None
    actor_kind: str = Field(min_length=1, max_length=32)  # user | service | system
    action: str = Field(min_length=1, max_length=128)  # e.g. 'alerts.claim'
    resource_kind: str = Field(min_length=1, max_length=64)
    resource_id: str | None = None
    purpose: Purpose
    request_id: str | None = None
    tenant_id: str = Field(default="mtn-ghana", min_length=1)
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)
