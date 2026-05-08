from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import PlainTextResponse, Response

from fraudnet.kafka.errors import DeliveryError
from fraudnet.obs import counter, get_logger, metrics_endpoint
from ingest_sms.adapter import SmscPushEvent, partition_key, to_canonical
from ingest_sms.deps import IngestDeps, deps_dependency

_log = get_logger("ingest_sms.api")

_RECEIVED = counter(
    "ingest_sms_received_total",
    "SMS events received.",
    labelnames=("kind",),
)
_REJECTED = counter(
    "ingest_sms_rejected_total",
    "SMS events rejected.",
    labelnames=("reason",),
)
_BODY_CAPTURED = counter(
    "ingest_sms_body_captured_total",
    "SMS events where body was captured (purpose-gated).",
)


router = APIRouter()


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


@router.post("/smsc/push", status_code=status.HTTP_202_ACCEPTED)
async def smsc_push(
    request: Request,
    deps: Annotated[IngestDeps, Depends(deps_dependency)],
    x_smsc_signature: Annotated[str | None, Header(alias="X-SMSC-Signature")] = None,
) -> dict[str, str]:
    raw = await request.body()

    if deps.settings.webhook_shared_secret:
        if not x_smsc_signature or not _verify(raw, x_smsc_signature, deps.settings.webhook_shared_secret):
            _REJECTED.labels(reason="bad_signature").inc()
            raise HTTPException(status_code=401, detail="invalid signature")

    try:
        push = SmscPushEvent.model_validate_json(raw)
    except Exception as exc:  # noqa: BLE001
        _REJECTED.labels(reason="parse_error").inc()
        _log.warning("sms.parse_failed", error=str(exc))
        raise HTTPException(status_code=400, detail="invalid SMSC payload") from exc

    try:
        event = to_canonical(
            push,
            source=deps.settings.smsc_id,
            smsc_id=deps.settings.smsc_id,
            allow_body_capture=deps.settings.allow_body_capture,
        )
    except ValueError as exc:
        _REJECTED.labels(reason="adapter_rejected").inc()
        _log.warning("sms.adapter_rejected", error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if event.body is not None:
        _BODY_CAPTURED.inc()

    if not await deps.idempotency.claim(event.event_id, ttl_s=deps.settings.idempotency_ttl_s):
        return {"status": "duplicate", "event_id": event.event_id}

    try:
        await deps.producer.send(event, key=partition_key(event))
    except DeliveryError as exc:
        _REJECTED.labels(reason="kafka_delivery_failed").inc()
        _log.error("sms.kafka_delivery_failed", error=str(exc))
        raise HTTPException(status_code=503, detail="kafka delivery failed") from exc

    _RECEIVED.labels(kind=event.kind).inc()
    return {"status": "accepted", "event_id": event.event_id}


def _verify(body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode("utf-8"), body, "sha256").hexdigest()
    return hmac.compare_digest(expected, signature.lower())
