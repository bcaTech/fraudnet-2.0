"""DNS-query scanner.

Subscribes to `data.events.v1` (kind=`dns_query`) and looks each queried
domain up against url-intel's blocklist. Blocked queries become
`data.dns_blocklist_hit` signals on `fraud.signals.v1`.

The scanner is opt-in (`URL_INTEL_URL` env). When unset the consumer is
not started — keeps brain-content's deployment surface lean.
"""

from __future__ import annotations

import asyncio
from time import time
from uuid import uuid4

import httpx

from fraudnet.kafka import AvroConsumer, AvroProducer, DLQRouter, KafkaSettings
from fraudnet.kafka.consumer import ConsumedMessage
from fraudnet.obs import counter, get_logger
from fraudnet.schemas.events import DataEventV1
from fraudnet.schemas.signals import SignalEventV1
from fraudnet.schemas.types import EntityKind, RiskScore, Severity, Subject
from brain_content.ott_domain_analysis import OttDomainAnalyser, OttDomainVerdict

_log = get_logger("brain_content.dns_scanner")


_DNS_LOOKUPS = counter(
    "brain_content_dns_lookups_total",
    "DNS queries scanned against url-intel.",
    labelnames=("blocked",),
)
_DNS_SIGNALS = counter(
    "brain_content_dns_signals_total",
    "DNS blocklist-hit signals emitted.",
    labelnames=("severity",),
)
_OTT_FLAGS = counter(
    "brain_content_ott_flags_total",
    "OTT domain analyser flags raised.",
    labelnames=("flag",),
)


SIGNAL_KIND = "data.dns_blocklist_hit"
OTT_SIGNAL_KIND = "data.ott_suspicious_domain"
MODEL_ID = "url-intel-dns-scan"
MODEL_VERSION = "0.1.0"


class DnsScanner:
    def __init__(
        self,
        *,
        url_intel_url: str,
        signal_producer: AvroProducer[SignalEventV1],
        kafka_settings_factory,
        timeout_s: float = 0.05,
        ott_analyser: OttDomainAnalyser | None = None,
    ) -> None:
        self._url_intel = url_intel_url.rstrip("/")
        self._producer = signal_producer
        self._make_settings = kafka_settings_factory
        self._timeout = timeout_s
        self._client: httpx.AsyncClient | None = None
        self._consumer: AvroConsumer[DataEventV1] | None = None
        self._ott = ott_analyser or OttDomainAnalyser()

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=self._timeout, base_url=self._url_intel)
        consumer: AvroConsumer[DataEventV1] = AvroConsumer(
            settings=self._make_settings("brain-content-dns"),
            topic="data.events.v1",
            model_cls=DataEventV1,
            dlq=DLQRouter(self._make_settings("brain-content-dns-dlq")),
        )
        self._consumer = consumer
        try:
            await consumer.run(self._on_event)
        except asyncio.CancelledError:
            await self.stop()
            raise

    async def stop(self) -> None:
        if self._consumer is not None:
            self._consumer.stop()
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _on_event(self, msg: ConsumedMessage[DataEventV1]) -> None:
        ev = msg.payload
        if ev.kind != "dns_query" or not ev.domain:
            return

        # 1. Heuristic OTT analyser (offline, sub-millisecond). Runs before
        # the network lookup so we still fire when url-intel is degraded.
        ott_verdict = self._ott.analyse(ev.domain, now_ms=ev.event_ts_ms)
        if ott_verdict.is_brand_lookalike:
            _OTT_FLAGS.labels(flag="brand_lookalike").inc()
        if ott_verdict.is_url_shortener:
            _OTT_FLAGS.labels(flag="url_shortener").inc()
        if ott_verdict.is_newly_registered:
            _OTT_FLAGS.labels(flag="newly_registered").inc()

        # 2. url-intel network lookup (authoritative blocklist).
        verdict = await self._lookup(ev.domain)
        _DNS_LOOKUPS.labels(blocked=str(verdict.get("blocked", False)).lower()).inc()

        if verdict.get("blocked"):
            confidence = float(verdict.get("confidence") or 0.9)
            severity = Severity.HIGH if confidence > 0.8 else Severity.MEDIUM
            signal = self._build_signal(
                ev=ev,
                verdict=verdict,
                confidence=confidence,
                severity=severity,
                ott_verdict=ott_verdict,
            )
            _DNS_SIGNALS.labels(severity=signal.severity.value).inc()
            key = str(ev.msisdn) if ev.msisdn else ev.domain
            await self._producer.send(signal, key=key)
            return

        # 3. OTT-only signal: url-intel didn't block but the heuristic did.
        # Brand-lookalike + shortener compose to HIGH; either alone is
        # MEDIUM; NRD-only is LOW (suppression on the consumer side will
        # de-dupe per-subject).
        if ott_verdict.is_suspicious:
            confidence, severity = _ott_confidence(ott_verdict)
            ott_signal = self._build_ott_signal(
                ev=ev,
                ott_verdict=ott_verdict,
                confidence=confidence,
                severity=severity,
            )
            _DNS_SIGNALS.labels(severity=ott_signal.severity.value).inc()
            key = str(ev.msisdn) if ev.msisdn else ev.domain
            await self._producer.send(ott_signal, key=key)

    async def _lookup(self, domain: str) -> dict[str, object]:
        assert self._client is not None
        try:
            r = await self._client.get("/blocklist/check", params={"url": domain})
            if r.status_code != 200:
                return {"blocked": False}
            return r.json()
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            _log.warning("dns_scanner.lookup_failed", domain=domain, error=str(exc))
            return {"blocked": False}

    def _build_signal(
        self,
        *,
        ev: DataEventV1,
        verdict: dict[str, object],
        confidence: float,
        severity: Severity,
        ott_verdict: OttDomainVerdict | None = None,
    ) -> SignalEventV1:
        now_ms = int(time() * 1000)
        evidence: dict[str, str | int | float | bool] = {
            "domain": ev.domain or "",
            "matched": str(verdict.get("matched") or ""),
            "category": str(verdict.get("category") or "unknown"),
            "source": str(verdict.get("source") or "url-intel"),
            "confidence": float(confidence),
        }
        if ev.msisdn is not None:
            evidence["msisdn"] = str(ev.msisdn)
        if ott_verdict is not None and ott_verdict.is_suspicious:
            evidence.update(ott_verdict.to_evidence())
        # Subject: the URL/domain. Downstream NOC can pivot to the msisdn
        # via evidence when investigating.
        subject = Subject(kind=EntityKind.URL, id=ev.domain or "")
        score = RiskScore(
            value=max(0.0, min(1.0, confidence)),
            model_id=MODEL_ID,
            model_version=MODEL_VERSION,
            computed_at_ms=now_ms,
        )
        return SignalEventV1(
            event_id=f"sig_{uuid4().hex[:24]}",
            event_ts_ms=now_ms,
            ingest_ts_ms=now_ms,
            source="brain-content:dns",
            tenant_id=ev.tenant_id,
            signal_kind=SIGNAL_KIND,
            subject=subject,
            score=score,
            severity=severity,
            evidence=evidence,
            suppression_key=f"{ev.tenant_id}:url:{ev.domain}:{SIGNAL_KIND}",
        )

    def _build_ott_signal(
        self,
        *,
        ev: DataEventV1,
        ott_verdict: OttDomainVerdict,
        confidence: float,
        severity: Severity,
    ) -> SignalEventV1:
        now_ms = int(time() * 1000)
        evidence = ott_verdict.to_evidence()
        evidence["confidence"] = float(confidence)
        if ev.msisdn is not None:
            evidence["msisdn"] = str(ev.msisdn)
        subject = Subject(kind=EntityKind.URL, id=ev.domain or "")
        score = RiskScore(
            value=max(0.0, min(1.0, confidence)),
            model_id=MODEL_ID,
            model_version=MODEL_VERSION,
            computed_at_ms=now_ms,
        )
        return SignalEventV1(
            event_id=f"sig_{uuid4().hex[:24]}",
            event_ts_ms=now_ms,
            ingest_ts_ms=now_ms,
            source="brain-content:ott",
            tenant_id=ev.tenant_id,
            signal_kind=OTT_SIGNAL_KIND,
            subject=subject,
            score=score,
            severity=severity,
            evidence=evidence,
            suppression_key=f"{ev.tenant_id}:url:{ev.domain}:{OTT_SIGNAL_KIND}",
        )


def _ott_confidence(verdict: OttDomainVerdict) -> tuple[float, Severity]:
    """Compose a confidence and severity from an OTT verdict.

    Brand-lookalike + shortener → 0.85 / HIGH. Single strong signal → 0.65 /
    MEDIUM. NRD-only → 0.30 / LOW.
    """
    strong = verdict.is_brand_lookalike or verdict.is_url_shortener
    multi = verdict.is_brand_lookalike and verdict.is_url_shortener
    if multi:
        return 0.85, Severity.HIGH
    if strong:
        return 0.65, Severity.MEDIUM
    return 0.30, Severity.LOW


def make_settings_factory(*, bootstrap: str, schema_registry_url: str, group_id: str):
    def factory(client_id: str) -> KafkaSettings:
        return KafkaSettings(
            bootstrap_servers=bootstrap,
            schema_registry_url=schema_registry_url,
            client_id=client_id,
            group_id=group_id,
        )

    return factory
