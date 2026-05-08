"""SMSC event adapter."""

from __future__ import annotations

import hashlib
from time import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from fraudnet.schemas.events import SmsEventV1
from fraudnet.schemas.types import MSISDN
from ingest_sms.normaliser import normalise

SmsKind = Literal["mt", "mo", "mt_delivery_receipt"]

_KIND_MAP: dict[str, SmsKind] = {
    "MT": "mt",
    "MOBILE_TERMINATED": "mt",
    "MO": "mo",
    "MOBILE_ORIGINATED": "mo",
    "MT_DR": "mt_delivery_receipt",
    "DELIVERY_RECEIPT": "mt_delivery_receipt",
}


class SmscPushEvent(BaseModel):
    """SMSC raw push payload."""

    model_config = ConfigDict(extra="allow")

    smsc_msg_id: str | None = None
    event_type: str = Field(min_length=1, max_length=32)
    timestamp_ms: int = Field(ge=0)
    sender: str
    recipient: str
    body: str | None = None
    short_code: str | None = None
    smsc_id: str | None = None


def to_canonical(
    raw: SmscPushEvent,
    *,
    source: str,
    smsc_id: str,
    allow_body_capture: bool,
    tenant_id: str = "mtn-ghana",
    event_id: str | None = None,
) -> SmsEventV1:
    kind = _KIND_MAP.get(raw.event_type.upper())
    if kind is None:
        raise ValueError(f"unknown SMS event_type: {raw.event_type!r}")

    body_hash: str | None = None
    template_hash: str | None = None
    if raw.body:
        n = normalise(raw.body)
        body_hash = n.body_hash
        template_hash = n.template_hash
    # URLs / template clusters are downstream concerns once they exist as a
    # field on SmsEventV1; for Phase 1 we keep them implicit in template_hash
    # and surface URL list only when brain-content asks for the body via
    # the audit-gated lookup path.

    return SmsEventV1(
        event_id=event_id or _derive_event_id(raw),
        event_ts_ms=raw.timestamp_ms,
        ingest_ts_ms=int(time() * 1000),
        source=source,
        tenant_id=tenant_id,
        kind=kind,
        sender=MSISDN(raw.sender),
        recipient=MSISDN(raw.recipient),
        body=raw.body if (allow_body_capture and raw.body) else None,
        body_hash=body_hash,
        template_hash=template_hash,
        short_code=raw.short_code,
        smsc_id=raw.smsc_id or smsc_id,
    )


def partition_key(event: SmsEventV1) -> str:
    """Partition on sender so a sender's SMS burst stays together for
    template-cluster computation downstream.
    """
    return event.sender


def _derive_event_id(raw: SmscPushEvent) -> str:
    if raw.smsc_msg_id:
        return f"sms_{raw.smsc_msg_id[:32]}"
    natural = f"{raw.sender}|{raw.recipient}|{raw.event_type}|{raw.timestamp_ms}".encode()
    return f"sms_{hashlib.sha256(natural).hexdigest()[:24]}"
