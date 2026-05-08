from __future__ import annotations

from fraudnet.schemas.types import MSISDN
from fraudnet.testing.factories import (
    fake_imei,
    fake_msisdn,
    fake_wallet_id,
    make_audit_event,
    make_momo_event,
    make_sms_event,
    make_voice_event,
)


def test_fake_msisdn_is_e164_parseable() -> None:
    raw = fake_msisdn()
    parsed = MSISDN(raw)
    assert parsed.startswith("+233")


def test_fake_imei_15_digits() -> None:
    assert len(fake_imei()) == 15
    assert fake_imei().isdigit()


def test_fake_wallet_id_is_stable_for_msisdn() -> None:
    wid = fake_wallet_id("+233241234567")
    assert wid == "W:233241234567"


def test_voice_factory_validates() -> None:
    ev = make_voice_event()
    assert ev.topic == "voice.events.v1"
    assert ev.caller.startswith("+233")
    assert ev.callee is not None and ev.callee.startswith("+233")


def test_sms_factory_does_not_leak_body() -> None:
    ev = make_sms_event()
    assert ev.body is None
    assert ev.body_hash is not None


def test_momo_factory_round_trip() -> None:
    ev = make_momo_event()
    assert ev.amount_minor == 5000
    assert ev.currency == "GHS"
    assert ev.kind.value == "p2p_transfer"


def test_audit_factory_uses_purpose_default() -> None:
    ev = make_audit_event()
    assert ev.purpose.value == "fraud_prevention"
