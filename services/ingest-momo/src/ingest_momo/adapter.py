"""Vendor adapter — translate MoMo BSS payloads into MoMoEventV1.

The contract is documented in `docs/data-contracts/momo-bss.md`. The adapter
is intentionally narrow: it accepts the BSS shape, rejects malformed input
loudly, and emits canonical events.

If MTN swaps the BSS vendor, this is the single integration point that
needs to change. Every new field added to MoMoEventV1 lands here first.
"""

from __future__ import annotations

from time import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from fraudnet.schemas.events import MoMoEventType, MoMoEventV1
from fraudnet.schemas.types import MSISDN

# Maps BSS event-type strings to the canonical MoMoEventType. Unknown values
# fail loudly — the adapter does not silently drop unknown traffic.
_BSS_KIND_MAP: dict[str, MoMoEventType] = {
    "P2P": MoMoEventType.P2P_TRANSFER,
    "P2P_TRANSFER": MoMoEventType.P2P_TRANSFER,
    "DEPOSIT": MoMoEventType.CASH_IN,
    "CASH_IN": MoMoEventType.CASH_IN,
    "WITHDRAWAL": MoMoEventType.CASH_OUT,
    "CASH_OUT": MoMoEventType.CASH_OUT,
    "BILL": MoMoEventType.BILL_PAYMENT,
    "MERCHANT": MoMoEventType.MERCHANT_PAYMENT,
    "BANK_TRANSFER": MoMoEventType.BANK_TRANSFER,
    "INTL_REMIT": MoMoEventType.INTERNATIONAL_REMITTANCE,
    "REVERSAL": MoMoEventType.REVERSAL,
}


class BssMoMoEvent(BaseModel):
    """Raw MoMo BSS event shape (minimal subset we depend on)."""

    model_config = ConfigDict(extra="allow")  # tolerate vendor extras; carry through metadata

    txn_id: str = Field(min_length=1, max_length=64)
    event_type: str = Field(min_length=1, max_length=32)
    timestamp_ms: int = Field(ge=0)
    sender_wallet_id: str | None = None
    recipient_wallet_id: str | None = None
    sender_msisdn: str | None = None
    recipient_msisdn: str | None = None
    amount_minor: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    counterparty_kind: str = Field(default="wallet")
    counterparty_account_hash: str | None = None
    is_reversal_of: str | None = None
    channel: str | None = None
    bss_metadata: dict[str, Any] | None = None


def to_canonical(
    bss: BssMoMoEvent,
    *,
    source: str,
    tenant_id: str = "mtn-ghana",
    event_id: str | None = None,
) -> MoMoEventV1:
    """Translate a BSS event to the canonical wire format.

    Raises:
        ValueError: BSS event_type unknown, MSISDN malformed, currency invalid.
    """
    kind = _BSS_KIND_MAP.get(bss.event_type.upper())
    if kind is None:
        raise ValueError(f"unknown MoMo BSS event_type: {bss.event_type!r}")

    return MoMoEventV1(
        event_id=event_id or _derive_event_id(bss),
        event_ts_ms=bss.timestamp_ms,
        ingest_ts_ms=int(time() * 1000),
        source=source,
        tenant_id=tenant_id,
        kind=kind,
        txn_id=bss.txn_id,
        sender_wallet_id=bss.sender_wallet_id,
        recipient_wallet_id=bss.recipient_wallet_id,
        sender_msisdn=MSISDN(bss.sender_msisdn) if bss.sender_msisdn else None,
        recipient_msisdn=MSISDN(bss.recipient_msisdn) if bss.recipient_msisdn else None,
        amount_minor=bss.amount_minor,
        currency=bss.currency.upper(),
        counterparty_kind=bss.counterparty_kind.lower(),  # type: ignore[arg-type]
        counterparty_account_hash=bss.counterparty_account_hash,
        is_reversal_of=bss.is_reversal_of,
        channel=bss.channel,  # type: ignore[arg-type]
    )


def partition_key(event: MoMoEventV1) -> str:
    """Partition key for `momo.events.v1`.

    Sender wallet wins so an outbound chain stays on one partition. For
    inbound-only events (cash-in from agent), recipient is used. Falls back
    to txn_id only as a last resort to avoid hot-partitioning the cluster.
    """
    return event.sender_wallet_id or event.recipient_wallet_id or event.txn_id


def _derive_event_id(bss: BssMoMoEvent) -> str:
    """Stable, idempotent event_id from BSS fields.

    BSS sometimes redelivers; we want the same `event_id` so downstream
    deduplication works. A hash of (txn_id, event_type, timestamp_ms) is
    sufficient because the BSS guarantees that triple is unique per event.
    """
    import hashlib

    raw = f"{bss.txn_id}|{bss.event_type}|{bss.timestamp_ms}".encode()
    return f"momo_{hashlib.sha256(raw).hexdigest()[:24]}"
