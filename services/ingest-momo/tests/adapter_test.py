"""Adapter — BSS payload → canonical MoMoEventV1."""

from __future__ import annotations

import pytest

from fraudnet.schemas.events import MoMoEventType
from ingest_momo.adapter import (
    BssMoMoEvent,
    _derive_event_id,
    partition_key,
    to_canonical,
)


def _bss(**overrides: object) -> BssMoMoEvent:
    payload = {
        "txn_id": "MTN-MOMO-ABC123",
        "event_type": "P2P",
        "timestamp_ms": 1_700_000_000_000,
        "sender_wallet_id": "W:233241234567",
        "recipient_wallet_id": "W:233207654321",
        "sender_msisdn": "0241234567",
        "recipient_msisdn": "0207654321",
        "amount_minor": 5000,
        "currency": "ghs",
        "counterparty_kind": "wallet",
    }
    payload.update(overrides)  # type: ignore[arg-type]
    return BssMoMoEvent.model_validate(payload)


class TestToCanonical:
    def test_p2p_round_trip(self) -> None:
        ev = to_canonical(_bss(), source="bss-prod")
        assert ev.kind == MoMoEventType.P2P_TRANSFER
        assert ev.amount_minor == 5000
        assert ev.currency == "GHS"  # uppercased
        assert ev.sender_msisdn == "+233241234567"
        assert ev.source == "bss-prod"
        assert ev.tenant_id == "mtn-ghana"

    def test_event_type_is_case_insensitive(self) -> None:
        ev = to_canonical(_bss(event_type="cash_in"), source="t")
        assert ev.kind == MoMoEventType.CASH_IN

    def test_unknown_event_type_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown MoMo BSS event_type"):
            to_canonical(_bss(event_type="MYSTERY"), source="t")

    def test_invalid_msisdn_rejected(self) -> None:
        with pytest.raises(ValueError):
            to_canonical(_bss(sender_msisdn="not-a-number"), source="t")

    def test_no_msisdns_is_fine(self) -> None:
        ev = to_canonical(
            _bss(sender_msisdn=None, recipient_msisdn=None),
            source="t",
        )
        assert ev.sender_msisdn is None
        assert ev.recipient_msisdn is None


class TestPartitionKey:
    def test_prefers_sender_wallet(self) -> None:
        ev = to_canonical(_bss(), source="t")
        assert partition_key(ev) == "W:233241234567"

    def test_falls_back_to_recipient(self) -> None:
        ev = to_canonical(_bss(sender_wallet_id=None), source="t")
        assert partition_key(ev) == "W:233207654321"

    def test_falls_back_to_txn_id_only_as_last_resort(self) -> None:
        ev = to_canonical(
            _bss(sender_wallet_id=None, recipient_wallet_id=None),
            source="t",
        )
        assert partition_key(ev) == ev.txn_id


class TestEventIdDerivation:
    def test_stable_for_same_inputs(self) -> None:
        a = _bss()
        b = _bss()
        assert _derive_event_id(a) == _derive_event_id(b)

    def test_changes_when_timestamp_changes(self) -> None:
        a = _derive_event_id(_bss(timestamp_ms=1_700_000_000_000))
        b = _derive_event_id(_bss(timestamp_ms=1_700_000_000_001))
        assert a != b

    def test_changes_when_event_type_changes(self) -> None:
        a = _derive_event_id(_bss(event_type="P2P"))
        b = _derive_event_id(_bss(event_type="CASH_IN"))
        assert a != b
