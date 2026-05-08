"""Factories for FraudNet domain types with realistic Ghanaian data."""

from __future__ import annotations

import secrets
from time import time
from typing import Any
from uuid import uuid4

from fraudnet.schemas.audit import AuditEventV1
from fraudnet.schemas.events import MoMoEventType, MoMoEventV1, SmsEventV1, VoiceEventV1
from fraudnet.schemas.types import Purpose

# MTN Ghana mobile prefixes (024, 054, 055, 059); Vodafone (020, 050);
# AirtelTigo (027, 057, 026, 056). We span carriers in factories so test
# events look like a real telco mix.
_GH_PREFIXES = ("0244", "0245", "0540", "0550", "0599", "0207", "0501", "0277", "0567")


def fake_msisdn(prefix: str | None = None) -> str:
    """Return a syntactically valid Ghanaian MSISDN as a 10-digit local string.

    Pass through MSISDN(...) at the call site if you want the canonical E.164
    form.
    """
    p = prefix or _GH_PREFIXES[secrets.randbelow(len(_GH_PREFIXES))]
    suffix = "".join(str(secrets.randbelow(10)) for _ in range(10 - len(p)))
    return p + suffix


def fake_imei() -> str:
    return "".join(str(secrets.randbelow(10)) for _ in range(15))


def fake_wallet_id(msisdn: str | None = None) -> str:
    msisdn = msisdn or fake_msisdn()
    digits = msisdn.lstrip("+").lstrip("0")
    return f"W:{digits}"


def _common(**overrides: Any) -> dict[str, Any]:
    base = {
        "event_id": f"ev_{uuid4().hex[:20]}",
        "event_ts_ms": int(time() * 1000),
        "ingest_ts_ms": int(time() * 1000),
        "source": "fraudnet.testing",
    }
    base.update(overrides)
    return base


def make_voice_event(**overrides: Any) -> VoiceEventV1:
    payload = {
        **_common(),
        "kind": "call_start",
        "caller": fake_msisdn(),
        "callee": fake_msisdn(),
        "duration_s": 42,
        "network": "VoLTE",
    }
    payload.update(overrides)
    return VoiceEventV1.model_validate(payload)


def make_sms_event(**overrides: Any) -> SmsEventV1:
    payload = {
        **_common(),
        "kind": "mt",
        "sender": fake_msisdn(),
        "recipient": fake_msisdn(),
        "body": None,  # gated; production fills this only with a purpose claim
        "body_hash": "sha256:" + "0" * 64,
    }
    payload.update(overrides)
    return SmsEventV1.model_validate(payload)


def make_momo_event(**overrides: Any) -> MoMoEventV1:
    sender = fake_msisdn()
    recipient = fake_msisdn()
    payload = {
        **_common(),
        "kind": MoMoEventType.P2P_TRANSFER,
        "txn_id": f"MTN-MOMO-{uuid4().hex[:12].upper()}",
        "sender_wallet_id": fake_wallet_id(sender),
        "recipient_wallet_id": fake_wallet_id(recipient),
        "sender_msisdn": sender,
        "recipient_msisdn": recipient,
        "amount_minor": 5000,  # 50.00 GHS
        "currency": "GHS",
        "counterparty_kind": "wallet",
    }
    payload.update(overrides)
    return MoMoEventV1.model_validate(payload)


def make_audit_event(
    *,
    action: str = "test.action",
    purpose: Purpose = Purpose.FRAUD_PREVENTION,
    **overrides: Any,
) -> AuditEventV1:
    payload = {
        "event_id": f"aud_{uuid4().hex[:20]}",
        "event_ts_ms": int(time() * 1000),
        "actor_kind": "user",
        "action": action,
        "resource_kind": "test_resource",
        "purpose": purpose,
    }
    payload.update(overrides)
    return AuditEventV1.model_validate(payload)
