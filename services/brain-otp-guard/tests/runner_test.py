"""Runner correlation tests.

We don't drive a real Kafka bus here — we exercise the two handler entry
points directly (`_on_voice` / `_on_sms`) with constructed events. The
producer is a small in-memory fake.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from fraudnet.kafka.consumer import ConsumedMessage
from fraudnet.schemas.events import SmsEventV1, VoiceEventV1
from fraudnet.schemas.signals import SignalEventV1
from brain_otp_guard.registry import (
    InMemoryActiveCallRegistry,
    InMemorySuppressionStore,
)
from brain_otp_guard.runner import OtpGuardRunner

BANK_CODES = frozenset({"ECOBANK", "MTN", "GCB"})


@dataclass
class FakeProducer:
    sent: list[SignalEventV1] = field(default_factory=list)

    async def send(self, payload: SignalEventV1, *, key: str | None = None) -> None:
        self.sent.append(payload)

    async def stop(self) -> None:
        return None


def _voice_msg(
    *,
    kind: str = "call_start",
    caller: str = "+233207777777",
    callee: str | None = "+233241234567",
    ts_ms: int = 1_700_000_000_000,
) -> ConsumedMessage[VoiceEventV1]:
    ev = VoiceEventV1(
        event_id="ev_voice_" + str(ts_ms),
        event_ts_ms=ts_ms,
        ingest_ts_ms=ts_ms,
        source="ingest-voice",
        kind=kind,  # type: ignore[arg-type]
        caller=caller,  # type: ignore[arg-type]
        callee=callee,  # type: ignore[arg-type]
    )
    return ConsumedMessage(
        payload=ev, key=None, topic="voice.events.v1", partition=0, offset=0, timestamp_ms=ts_ms
    )


def _sms_msg(
    *,
    recipient: str = "+233241234567",
    body: str | None = "Your OTP is 123456. Do not share.",
    short_code: str | None = "ECOBANK",
    ts_ms: int = 1_700_000_001_000,
    kind: str = "mt",
) -> ConsumedMessage[SmsEventV1]:
    ev = SmsEventV1(
        event_id="ev_sms_" + str(ts_ms),
        event_ts_ms=ts_ms,
        ingest_ts_ms=ts_ms,
        source="ingest-sms",
        kind=kind,  # type: ignore[arg-type]
        sender="+233231000000",  # type: ignore[arg-type]
        recipient=recipient,  # type: ignore[arg-type]
        body=body,
        short_code=short_code,
    )
    return ConsumedMessage(
        payload=ev, key=None, topic="sms.events.v1", partition=0, offset=0, timestamp_ms=ts_ms
    )


def _runner(
    *,
    suppression=None,
) -> tuple[OtpGuardRunner, FakeProducer]:
    reg = InMemoryActiveCallRegistry(ttl_s=900)
    sup = suppression or InMemorySuppressionStore(window_s=300)
    prod = FakeProducer()
    runner = OtpGuardRunner(
        registry=reg,
        suppression=sup,
        signal_producer=prod,  # type: ignore[arg-type]
        bank_short_codes=BANK_CODES,
        kafka_settings_factory=lambda _client_id: None,
    )
    return runner, prod


class TestCorrelation:
    @pytest.mark.asyncio
    async def test_otp_during_active_call_fires_critical_signal(self) -> None:
        runner, prod = _runner()
        await runner._on_voice(_voice_msg(kind="call_start"))
        await runner._on_sms(_sms_msg())
        assert len(prod.sent) == 1
        sig = prod.sent[0]
        assert sig.signal_kind == "otp.during_call"
        assert sig.severity.value == "critical"
        assert sig.subject.id == "+233241234567"
        assert sig.evidence["caller"] == "+233207777777"

    @pytest.mark.asyncio
    async def test_otp_without_active_call_does_not_fire(self) -> None:
        runner, prod = _runner()
        await runner._on_sms(_sms_msg())
        assert prod.sent == []

    @pytest.mark.asyncio
    async def test_call_ended_before_sms_does_not_fire(self) -> None:
        runner, prod = _runner()
        await runner._on_voice(_voice_msg(kind="call_start"))
        await runner._on_voice(_voice_msg(kind="call_end"))
        await runner._on_sms(_sms_msg())
        assert prod.sent == []

    @pytest.mark.asyncio
    async def test_non_otp_sms_during_call_does_not_fire(self) -> None:
        runner, prod = _runner()
        await runner._on_voice(_voice_msg(kind="call_start"))
        await runner._on_sms(_sms_msg(body="Thanks for shopping!", short_code=None))
        assert prod.sent == []

    @pytest.mark.asyncio
    async def test_mo_sms_is_ignored(self) -> None:
        runner, prod = _runner()
        await runner._on_voice(_voice_msg(kind="call_start"))
        await runner._on_sms(_sms_msg(kind="mo"))
        assert prod.sent == []

    @pytest.mark.asyncio
    async def test_suppression_within_window(self) -> None:
        runner, prod = _runner()
        await runner._on_voice(_voice_msg(kind="call_start"))
        await runner._on_sms(_sms_msg())
        await runner._on_sms(_sms_msg(ts_ms=1_700_000_002_000))
        assert len(prod.sent) == 1

    @pytest.mark.asyncio
    async def test_suppression_releases_after_window(self) -> None:
        clock = [0.0]
        sup = InMemorySuppressionStore(window_s=300, clock=lambda: clock[0])
        runner, prod = _runner(suppression=sup)
        await runner._on_voice(_voice_msg(kind="call_start"))
        await runner._on_sms(_sms_msg())
        clock[0] = 301.0
        await runner._on_sms(_sms_msg(ts_ms=1_700_000_400_000))
        assert len(prod.sent) == 2

    @pytest.mark.asyncio
    async def test_voice_call_without_callee_is_ignored(self) -> None:
        runner, prod = _runner()
        await runner._on_voice(_voice_msg(kind="registration", callee=None))
        await runner._on_sms(_sms_msg())
        assert prod.sent == []
