"""Auto-populator — translates fraud.signals.v1 into intel_entries.

A subset of `signal_kind` values map to repository entries:

    signal_kind                    → kind                    identifier
    -------------------------------------------------------------------
    voice.velocity_burst           → suspect_number          subject.id
    sms.bulk_template              → suspect_number          subject.id
    device.imei_churn              → suspect_number          subject.id
    momo.mule_velocity             → suspect_number          subject.id
    sms.template_smishing          → scam_template           details.template_hash
    sms.malicious_url              → scam_template           details.url_hash
    cli.spoof_validation_failed    → spoof_indicator         subject.id
    aml.watchlist_match            → suspect_number          subject.id
    agent.commission_farming       → agent_risk              subject.id
    agent.split_txn                → agent_risk              subject.id
    agent.collusion                → agent_risk              subject.id

Other signal_kinds are skipped — the repo focuses on entries that
make sense as enrichment lookups for future scoring rounds.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fraudnet.kafka import AvroConsumer, DLQRouter
from fraudnet.kafka.consumer import ConsumedMessage
from fraudnet.obs import counter, get_logger
from fraudnet.schemas.signals import SignalEventV1

from intel_repository.repo import IntelRepo
from intel_repository.settings import Settings

_log = get_logger("intel_repository.populator")
_INGESTED = counter(
    "intel_repository_ingested_total",
    "Signals folded into intel_entries.",
    labelnames=("kind", "outcome"),
)


# Mapping for the auto-populator. Keys are signal_kind; values are
# (intel_kind, identifier_extractor).


def _subject_id(s: SignalEventV1) -> str | None:
    return s.subject.id if s.subject else None


def _template_hash(s: SignalEventV1) -> str | None:
    h = s.evidence.get("template_hash") or s.evidence.get("body_hash")
    return str(h) if h else None


def _url_hash(s: SignalEventV1) -> str | None:
    h = s.evidence.get("url_hash") or s.evidence.get("domain")
    return str(h) if h else None


_SIGNAL_TO_INTEL: dict[str, tuple[str, callable]] = {
    "voice.velocity_burst": ("suspect_number", _subject_id),
    "sms.bulk_template": ("suspect_number", _subject_id),
    "device.imei_churn": ("suspect_number", _subject_id),
    "momo.mule_velocity": ("suspect_number", _subject_id),
    "momo.high_value_velocity": ("suspect_number", _subject_id),
    "aml.watchlist_match": ("suspect_number", _subject_id),
    "sms.template_smishing": ("scam_template", _template_hash),
    "sms.known_bad_template": ("scam_template", _template_hash),
    "sms.known_bad_body": ("scam_template", _template_hash),
    "sms.malicious_url": ("scam_template", _url_hash),
    "cli.spoof_validation_failed": ("spoof_indicator", _subject_id),
    "agent.commission_farming": ("agent_risk", _subject_id),
    "agent.split_txn": ("agent_risk", _subject_id),
    "agent.collusion": ("agent_risk", _subject_id),
    "agent.float_manipulation": ("agent_risk", _subject_id),
    "agent.phantom_customer": ("agent_risk", _subject_id),
}


class IntelPopulator:
    def __init__(
        self,
        *,
        settings: Settings,
        repo: IntelRepo,
        kafka_settings_factory,
    ) -> None:
        self._settings = settings
        self._repo = repo
        self._make_settings = kafka_settings_factory
        self._stop = asyncio.Event()
        self._consumer: object | None = None

    async def start(self) -> None:
        consumer = AvroConsumer(
            settings=self._make_settings("intel-repository"),
            topic="fraud.signals.v1",
            model_cls=SignalEventV1,
            dlq=DLQRouter(self._make_settings("intel-repository-dlq")),
        )
        self._consumer = consumer
        await consumer.run(self._on_signal)

    async def stop(self) -> None:
        self._stop.set()
        if self._consumer is not None:
            self._consumer.stop()  # type: ignore[attr-defined]

    async def _on_signal(self, msg: ConsumedMessage[SignalEventV1]) -> None:
        sig = msg.payload
        mapping = _SIGNAL_TO_INTEL.get(sig.signal_kind)
        if mapping is None:
            return
        intel_kind, extractor = mapping
        identifier = extractor(sig)
        if not identifier:
            _INGESTED.labels(kind=intel_kind, outcome="no_identifier").inc()
            return
        ttl_s = self._ttl_for(intel_kind)
        try:
            await self._repo.upsert_entry(
                kind=intel_kind,
                identifier=identifier,
                risk_score=float(sig.score.value),
                ttl_s=ttl_s,
                contributor=sig.source,
                metadata={
                    "signal_kind": sig.signal_kind,
                    "severity": sig.severity.value,
                    "explanation": sig.explanation_text or "",
                    "model_id": sig.score.model_id,
                },
                tenant_id=sig.tenant_id,
            )
            _INGESTED.labels(kind=intel_kind, outcome="ok").inc()
        except Exception as exc:  # noqa: BLE001
            _INGESTED.labels(kind=intel_kind, outcome="error").inc()
            _log.warning(
                "intel_repository.populator.upsert_failed",
                kind=intel_kind,
                error=str(exc),
            )

    def _ttl_for(self, kind: str) -> int:
        if kind == "scam_template":
            return self._settings.ttl_scam_template_s
        if kind == "spoof_indicator":
            return self._settings.ttl_spoof_indicator_s
        return self._settings.ttl_default_s


# Suppress unused
_ = Any
