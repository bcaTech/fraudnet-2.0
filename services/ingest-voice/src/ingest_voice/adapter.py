"""Vendor-neutral probe adapter.

The `VoiceProbeAdapter` interface lets us swap probe vendors once the RFI
selection lands (Polystar / Subex / NetScout / EXFO). For Phase 1 we ship a
generic JSON adapter and a stub for SS7-style CDRs; vendor-specific shims
can be added without touching the canonical model.
"""

from __future__ import annotations

import hashlib
from time import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from fraudnet.schemas.events import VoiceEventV1
from fraudnet.schemas.types import MSISDN

VoiceKind = Literal["call_start", "call_end", "registration", "handover"]

# Generic vendor event-type mapping. Vendors using non-standard kinds layer
# their shim on top of this module.
_VENDOR_KIND_MAP: dict[str, VoiceKind] = {
    "CALL_START": "call_start",
    "CALL_END": "call_end",
    "REGISTRATION": "registration",
    "REGISTER": "registration",
    "HANDOVER": "handover",
    "HANDOFF": "handover",
}


class GenericProbeEvent(BaseModel):
    """Vendor-neutral probe event shape.

    Polystar / Subex / NetScout shims map their native format onto this. The
    `vendor_meta` carries vendor extras through to the canonical event for
    downstream debugging.
    """

    model_config = ConfigDict(extra="allow")

    cdr_id: str | None = None
    event_type: str = Field(min_length=1, max_length=32)
    timestamp_ms: int = Field(ge=0)
    caller: str
    callee: str | None = None
    imsi: str | None = None
    imei: str | None = None
    duration_s: int | None = Field(default=None, ge=0)
    cell_id: str | None = None
    location_area_code: str | None = None
    network: Literal["2G", "3G", "4G", "5G", "VoLTE", "VoWiFi"] | None = None
    vendor_meta: dict[str, str] | None = None


def to_canonical(
    raw: GenericProbeEvent,
    *,
    source: str,
    tenant_id: str = "mtn-ghana",
    event_id: str | None = None,
) -> VoiceEventV1:
    kind = _VENDOR_KIND_MAP.get(raw.event_type.upper())
    if kind is None:
        raise ValueError(f"unknown voice event_type: {raw.event_type!r}")

    return VoiceEventV1(
        event_id=event_id or _derive_event_id(raw),
        event_ts_ms=raw.timestamp_ms,
        ingest_ts_ms=int(time() * 1000),
        source=source,
        tenant_id=tenant_id,
        kind=kind,
        caller=MSISDN(raw.caller),
        callee=MSISDN(raw.callee) if raw.callee else None,
        imsi=raw.imsi,
        imei=raw.imei,
        duration_s=raw.duration_s,
        cell_id=raw.cell_id,
        location_area_code=raw.location_area_code,
        network=raw.network,
        vendor_meta=raw.vendor_meta or {},
    )


def partition_key(event: VoiceEventV1) -> str:
    """Partition on caller MSISDN so a number's call chain stays in-order."""
    return event.caller


def _derive_event_id(raw: GenericProbeEvent) -> str:
    """Stable id from (cdr_id|caller, event_type, timestamp_ms).

    Vendors that supply a `cdr_id` use it directly; otherwise we fall back to
    a hash of the natural keys. CDRs are redelivered on probe restart, so
    stable ids matter for the idempotency cache.
    """
    if raw.cdr_id:
        return f"voice_{raw.cdr_id[:32]}"
    natural = f"{raw.caller}|{raw.callee or ''}|{raw.event_type}|{raw.timestamp_ms}".encode()
    return f"voice_{hashlib.sha256(natural).hexdigest()[:24]}"


def normalise_vendor_kind(vendor_kind: str) -> str:
    """Public helper for vendor shims that want to extend the kind map."""
    return _VENDOR_KIND_MAP.get(vendor_kind.upper(), vendor_kind.lower())


# Re-exported for tests.
__all__ = [
    "GenericProbeEvent",
    "_VENDOR_KIND_MAP",
    "_derive_event_id",
    "partition_key",
    "to_canonical",
]


# Type alias for adapter implementations
class VoiceProbeAdapter:
    """Hook point for vendor-specific shims.

    Implementations translate vendor payloads to GenericProbeEvent. The shim
    is a one-method protocol: `parse(raw_bytes) -> GenericProbeEvent`. Use
    Pydantic to declare the vendor's input shape and feed its dict into
    GenericProbeEvent.model_validate.
    """

    def parse(self, raw: bytes) -> GenericProbeEvent:  # pragma: no cover — abstract
        raise NotImplementedError

    def vendor_id(self) -> str:  # pragma: no cover
        raise NotImplementedError


class GenericJsonAdapter(VoiceProbeAdapter):
    """Default adapter: accepts the GenericProbeEvent shape directly as JSON."""

    def __init__(self, vendor_id: str = "generic") -> None:
        self._vendor = vendor_id

    def parse(self, raw: bytes) -> GenericProbeEvent:
        return GenericProbeEvent.model_validate_json(raw)

    def vendor_id(self) -> str:
        return self._vendor


# Type for vendor_meta values — keep alongside the canonical event.
VendorMeta = dict[str, str]
_ = Any  # silence unused-import — Any kept for future shim type aliases
