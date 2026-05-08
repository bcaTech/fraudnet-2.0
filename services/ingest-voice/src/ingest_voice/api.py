"""FastAPI routes for ingest-voice.

Endpoints:
  POST /probe/voice     — vendor probe push (HMAC-authenticated)
  GET  /health/{live,ready}
  GET  /metrics
"""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import PlainTextResponse, Response

from fraudnet.kafka.errors import DeliveryError
from fraudnet.obs import counter, get_logger, metrics_endpoint
from ingest_voice.adapter import (
    GenericJsonAdapter,
    partition_key,
    to_canonical,
)
from ingest_voice.deps import IngestDeps, deps_dependency

_log = get_logger("ingest_voice.api")

_RECEIVED = counter(
    "ingest_voice_probe_received_total",
    "Voice probe events received.",
    labelnames=("kind",),
)
_REJECTED = counter(
    "ingest_voice_probe_rejected_total",
    "Voice probe events rejected.",
    labelnames=("reason",),
)


router = APIRouter()
_default_adapter = GenericJsonAdapter(vendor_id="generic")


@router.get("/health/live", include_in_schema=False)
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", include_in_schema=False)
async def readiness(deps: Annotated[IngestDeps, Depends(deps_dependency)]) -> dict[str, str]:
    if deps.producer is None:
        raise HTTPException(status_code=503, detail="kafka producer not initialised")
    return {"status": "ready", "service": deps.settings.service_name}


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    body, content_type = metrics_endpoint()()
    return PlainTextResponse(body, media_type=content_type)


@router.post("/probe/voice", status_code=status.HTTP_202_ACCEPTED)
async def voice_webhook(
    request: Request,
    deps: Annotated[IngestDeps, Depends(deps_dependency)],
    x_probe_signature: Annotated[str | None, Header(alias="X-Probe-Signature")] = None,
) -> dict[str, str]:
    raw = await request.body()

    if deps.settings.webhook_shared_secret:
        if not x_probe_signature or not _verify(raw, x_probe_signature, deps.settings.webhook_shared_secret):
            _REJECTED.labels(reason="bad_signature").inc()
            raise HTTPException(status_code=401, detail="invalid signature")

    try:
        probe = _default_adapter.parse(raw)
    except Exception as exc:  # noqa: BLE001
        _REJECTED.labels(reason="parse_error").inc()
        _log.warning("voice.parse_failed", error=str(exc))
        raise HTTPException(status_code=400, detail="invalid probe payload") from exc

    try:
        event = to_canonical(probe, source=deps.settings.vendor_id)
    except ValueError as exc:
        _REJECTED.labels(reason="adapter_rejected").inc()
        _log.warning("voice.adapter_rejected", error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not await deps.idempotency.claim(event.event_id, ttl_s=deps.settings.idempotency_ttl_s):
        _log.info("voice.duplicate_suppressed", event_id=event.event_id)
        return {"status": "duplicate", "event_id": event.event_id}

    try:
        await deps.producer.send(event, key=partition_key(event))
    except DeliveryError as exc:
        _REJECTED.labels(reason="kafka_delivery_failed").inc()
        _log.error("voice.kafka_delivery_failed", error=str(exc))
        raise HTTPException(status_code=503, detail="kafka delivery failed") from exc

    _RECEIVED.labels(kind=event.kind).inc()
    return {"status": "accepted", "event_id": event.event_id}


def _verify(body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode("utf-8"), body, "sha256").hexdigest()
    return hmac.compare_digest(expected, signature.lower())
