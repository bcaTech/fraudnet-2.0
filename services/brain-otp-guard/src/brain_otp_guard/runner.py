"""Async runner — consumes voice + sms events, emits OTP-during-call signals.

Two consumer loops run concurrently:
  - voice.events.v1 → registry.start / registry.end
  - sms.events.v1   → OTP detection + active-call lookup

The signal emitted is `otp.during_call` with severity CRITICAL. Decisions
turns this into the Tier-1 `otp.hold_and_alert` action via policy.
"""

from __future__ import annotations

import asyncio
from time import time
from uuid import uuid4

from fraudnet.kafka import AvroConsumer, AvroProducer, DLQRouter, KafkaSettings
from fraudnet.kafka.consumer import ConsumedMessage
from fraudnet.obs import counter, get_logger
from fraudnet.schemas.events import SmsEventV1, VoiceEventV1
from fraudnet.schemas.signals import SignalEventV1
from fraudnet.schemas.types import EntityKind, RiskScore, Severity, Subject
from brain_otp_guard.detector import detect_otp
from brain_otp_guard.registry import ActiveCallRegistry, SuppressionStore

_log = get_logger("brain_otp_guard.runner")

MODEL_ID = "otp-guard-heuristic"
MODEL_VERSION = "0.1.0"
SIGNAL_KIND = "otp.during_call"


_VOICE_EVENTS = counter(
    "brain_otp_guard_voice_events_total",
    "Voice events processed by OTP-guard.",
    labelnames=("kind",),
)
_SMS_EVENTS = counter(
    "brain_otp_guard_sms_events_total",
    "MT SMS events processed by OTP-guard.",
    labelnames=("is_otp", "active_call", "fired"),
)
_SIGNALS = counter(
    "brain_otp_guard_signals_total",
    "OTP-during-call signals emitted.",
    labelnames=("severity",),
)


class OtpGuardRunner:
    def __init__(
        self,
        *,
        registry: ActiveCallRegistry,
        suppression: SuppressionStore,
        signal_producer: AvroProducer[SignalEventV1],
        bank_short_codes: frozenset[str],
        kafka_settings_factory,
    ) -> None:
        self._registry = registry
        self._suppression = suppression
        self._producer = signal_producer
        self._bank_short_codes = bank_short_codes
        self._make_settings = kafka_settings_factory
        self._stop = asyncio.Event()
        self._consumers: list[object] = []
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        voice_consumer = AvroConsumer(
            settings=self._make_settings("brain-otp-guard-voice"),
            topic="voice.events.v1",
            model_cls=VoiceEventV1,
            dlq=DLQRouter(self._make_settings("brain-otp-guard-voice-dlq")),
        )
        sms_consumer = AvroConsumer(
            settings=self._make_settings("brain-otp-guard-sms"),
            topic="sms.events.v1",
            model_cls=SmsEventV1,
            dlq=DLQRouter(self._make_settings("brain-otp-guard-sms-dlq")),
        )
        self._consumers = [voice_consumer, sms_consumer]
        self._tasks = [
            asyncio.create_task(voice_consumer.run(self._on_voice), name="otp-voice"),
            asyncio.create_task(sms_consumer.run(self._on_sms), name="otp-sms"),
        ]
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def stop(self) -> None:
        self._stop.set()
        for c in self._consumers:
            try:
                c.stop()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
        for t in self._tasks:
            t.cancel()
        await self._registry.aclose()
        await self._suppression.aclose()
        await self._producer.stop()

    async def _on_voice(self, msg: ConsumedMessage[VoiceEventV1]) -> None:
        ev = msg.payload
        _VOICE_EVENTS.labels(kind=ev.kind).inc()
        # We track the *callee* — the recipient of an inbound call is the
        # MSISDN whose OTP would be intercepted in a vishing scam.
        if ev.callee is None:
            return
        if ev.kind == "call_start":
            await self._registry.start(
                callee=str(ev.callee),
                caller=str(ev.caller),
                started_at_ms=ev.event_ts_ms,
            )
        elif ev.kind == "call_end":
            await self._registry.end(str(ev.callee))

    async def _on_sms(self, msg: ConsumedMessage[SmsEventV1]) -> None:
        ev = msg.payload
        if ev.kind != "mt":
            return

        result = detect_otp(
            body=ev.body,
            short_code=ev.short_code,
            bank_short_codes=self._bank_short_codes,
        )

        active_call = None
        if result.is_otp:
            active_call = await self._registry.get(str(ev.recipient))

        fired = bool(result.is_otp and active_call is not None)
        _SMS_EVENTS.labels(
            is_otp=str(result.is_otp).lower(),
            active_call=str(active_call is not None).lower(),
            fired=str(fired).lower(),
        ).inc()

        if not fired:
            return
        assert active_call is not None  # for type narrowing

        # Suppress repeats for the same recipient inside the window.
        suppression_key = f"{ev.tenant_id}:number:{ev.recipient}:{SIGNAL_KIND}"
        if await self._suppression.should_suppress(suppression_key):
            return

        signal = self._build_signal(
            ev=ev,
            caller=active_call.caller,
            confidence=result.confidence,
            short_code=result.matched_short_code,
            keyword_hits=len(result.matched_keywords),
        )
        _SIGNALS.labels(severity=signal.severity.value).inc()
        await self._producer.send(signal, key=str(ev.recipient))
        _log.info(
            "otp_guard.signal_emitted",
            recipient=str(ev.recipient),
            caller=active_call.caller,
            confidence=result.confidence,
            short_code=result.matched_short_code,
        )

    def _build_signal(
        self,
        *,
        ev: SmsEventV1,
        caller: str,
        confidence: float,
        short_code: str | None,
        keyword_hits: int,
    ) -> SignalEventV1:
        now_ms = int(time() * 1000)
        evidence: dict[str, str | int | float | bool] = {
            "caller": caller,
            "sms_sender": str(ev.sender),
            "confidence": float(confidence),
            "keyword_hits": keyword_hits,
        }
        if short_code:
            evidence["short_code"] = short_code
        if ev.smsc_id:
            evidence["smsc_id"] = ev.smsc_id
        score = RiskScore(
            value=max(0.0, min(1.0, confidence)),
            model_id=MODEL_ID,
            model_version=MODEL_VERSION,
            computed_at_ms=now_ms,
            feature_attribution={"confidence": float(confidence)},
        )
        return SignalEventV1(
            event_id=f"sig_{uuid4().hex[:24]}",
            event_ts_ms=now_ms,
            ingest_ts_ms=now_ms,
            source="brain-otp-guard",
            tenant_id=ev.tenant_id,
            signal_kind=SIGNAL_KIND,
            subject=Subject(kind=EntityKind.NUMBER, id=str(ev.recipient)),
            score=score,
            severity=Severity.CRITICAL,
            evidence=evidence,
            suppression_key=f"{ev.tenant_id}:number:{ev.recipient}:{SIGNAL_KIND}",
        )


def make_settings_factory(
    *, bootstrap: str, schema_registry_url: str, group_id: str,
):
    def factory(client_id: str) -> KafkaSettings:
        return KafkaSettings(
            bootstrap_servers=bootstrap,
            schema_registry_url=schema_registry_url,
            client_id=client_id,
            group_id=group_id,
        )

    return factory
