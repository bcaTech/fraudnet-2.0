"""Tests for canonical event types."""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from fraudnet.schemas.events import MoMoEventType, MoMoEventV1, SmsEventV1, VoiceEventV1


def _common_kw() -> dict[str, object]:
    return {
        "event_id": "01J7N4Z9X4G6XZRQ4N3V8B5RYM",
        "event_ts_ms": 1_700_000_000_000,
        "ingest_ts_ms": 1_700_000_000_500,
        "source": "test",
    }


class TestMoMoEventV1:
    def test_minimal_p2p(self) -> None:
        ev = MoMoEventV1(
            **_common_kw(),
            kind=MoMoEventType.P2P_TRANSFER,
            txn_id="MTN-2026-04-01-000001",
            sender_wallet_id="W:233241234567",
            recipient_wallet_id="W:233207654321",
            amount_minor=5000,
            currency="GHS",
            counterparty_kind="wallet",
        )
        assert ev.topic == "momo.events.v1"
        assert ev.amount_minor == 5000

    def test_currency_must_be_iso_4217(self) -> None:
        with pytest.raises(PydanticValidationError):
            MoMoEventV1(
                **_common_kw(),
                kind=MoMoEventType.P2P_TRANSFER,
                txn_id="X",
                sender_wallet_id="W:1",
                recipient_wallet_id="W:2",
                amount_minor=100,
                currency="ghs",  # lowercase rejected
                counterparty_kind="wallet",
            )

    def test_negative_amount_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            MoMoEventV1(
                **_common_kw(),
                kind=MoMoEventType.P2P_TRANSFER,
                txn_id="X",
                sender_wallet_id="W:1",
                recipient_wallet_id="W:2",
                amount_minor=-1,
                currency="GHS",
                counterparty_kind="wallet",
            )

    def test_extra_fields_forbidden(self) -> None:
        # Frozen + extra=forbid means schema drift fails loudly at parse time.
        with pytest.raises(PydanticValidationError):
            MoMoEventV1(
                **_common_kw(),
                kind=MoMoEventType.P2P_TRANSFER,
                txn_id="X",
                amount_minor=0,
                currency="GHS",
                counterparty_kind="wallet",
                ghost_field="not_allowed",  # type: ignore[call-arg]
            )


class TestVoiceEventV1:
    def test_call_start_minimal(self) -> None:
        ev = VoiceEventV1(
            **_common_kw(),
            kind="call_start",
            caller="0241234567",
            callee="0207654321",
            network="VoLTE",
        )
        assert str(ev.caller) == "+233241234567"
        assert ev.network == "VoLTE"

    def test_msisdn_validated(self) -> None:
        with pytest.raises(PydanticValidationError):
            VoiceEventV1(
                **_common_kw(),
                kind="call_start",
                caller="not-a-number",
            )


class TestSmsEventV1:
    def test_no_body_when_no_purpose(self) -> None:
        # Schema does not enforce the gate (that's audit-lib's job at the call
        # site). It does enforce that body is optional.
        ev = SmsEventV1(
            **_common_kw(),
            kind="mt",
            sender="0241234567",
            recipient="0207654321",
            body_hash="sha256:" + "0" * 64,
        )
        assert ev.body is None
        assert ev.body_hash is not None
