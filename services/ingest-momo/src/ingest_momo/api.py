"""FastAPI routes for ingest-momo.

Endpoints:
  POST /webhooks/momo   — primary BSS push receiver
  GET  /health/live     — liveness (process up)
  GET  /health/ready    — readiness (Kafka + dependencies healthy)
  GET  /metrics         — Prometheus scrape
"""

from __future__ import annotations

import hmac
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import PlainTextResponse, Response

from fraudnet.kafka.errors import DeliveryError
from fraudnet.obs import counter, get_logger, get_request_id, metrics_endpoint
from fraudnet.schemas.errors import ErrorEnvelope, ErrorBody, ErrorCode
from ingest_momo.adapter import BssMoMoEvent, partition_key, to_canonical
from ingest_momo.deps import IngestDeps, deps_dependency

_log = get_logger("ingest_momo.api")

_RECEIVED = counter(
    "ingest_momo_webhook_received_total",
    "MoMo webhook events received.",
    labelnames=("kind",),
)
_REJECTED = counter(
    "ingest_momo_webhook_rejected_total",
    "MoMo webhook events rejected (bad input or auth).",
    labelnames=("reason",),
)


router = APIRouter()


@router.get("/health/live", include_in_schema=False)
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", include_in_schema=False)
async def readiness(deps: Annotated[IngestDeps, Depends(deps_dependency)]) -> dict[str, Any]:
    # Producer.start() validates schema-registry connectivity at startup; here
    # we cheaply confirm the Kafka producer object is wired.
    if deps.producer is None:
        raise HTTPException(status_code=503, detail="kafka producer not initialised")
    return {"status": "ready", "service": deps.settings.service_name}


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    body, content_type = metrics_endpoint()()
    return PlainTextResponse(body, media_type=content_type)


@router.post("/webhooks/momo", status_code=status.HTTP_202_ACCEPTED)
async def momo_webhook(
    request: Request,
    deps: Annotated[IngestDeps, Depends(deps_dependency)],
    x_momo_signature: Annotated[str | None, Header(alias="X-MoMo-Signature")] = None,
) -> dict[str, str]:
    raw = await request.body()

    # 1. Authenticate the webhook. Production uses HMAC; dev with empty
    #    secret accepts unsigned (controlled by FRAUDNET_ENV).
    if deps.settings.webhook_shared_secret:
        if not x_momo_signature or not _verify_signature(
            raw, x_momo_signature, deps.settings.webhook_shared_secret
        ):
            _REJECTED.labels(reason="bad_signature").inc()
            raise HTTPException(status_code=401, detail="invalid signature")

    # 2. Parse the BSS payload.
    try:
        bss = BssMoMoEvent.model_validate_json(raw)
    except Exception as exc:  # noqa: BLE001 — narrow at the boundary
        _REJECTED.labels(reason="parse_error").inc()
        _log.warning("momo.parse_failed", error=str(exc))
        return _error_response(
            status.HTTP_400_BAD_REQUEST,
            code=ErrorCode.VALIDATION_FAILED,
            message="invalid MoMo BSS payload",
        )

    # 3. Translate to canonical.
    try:
        event = to_canonical(bss, source="momo-bss")
    except ValueError as exc:
        _REJECTED.labels(reason="adapter_rejected").inc()
        _log.warning("momo.adapter_rejected", error=str(exc))
        return _error_response(
            status.HTTP_400_BAD_REQUEST,
            code=ErrorCode.VALIDATION_FAILED,
            message=str(exc),
        )

    # 4. Idempotency check.
    if not await deps.idempotency.claim(event.event_id, ttl_s=deps.settings.idempotency_ttl_s):
        _log.info("momo.duplicate_suppressed", event_id=event.event_id)
        return {"status": "duplicate", "event_id": event.event_id}

    # 5. Publish.
    try:
        await deps.producer.send(event, key=partition_key(event))
    except DeliveryError as exc:
        _REJECTED.labels(reason="kafka_delivery_failed").inc()
        _log.error("momo.kafka_delivery_failed", error=str(exc))
        # 5xx so the BSS retries — we'd rather see duplicates dedup'd by
        # idempotency than lose a real event.
        raise HTTPException(status_code=503, detail="kafka delivery failed") from exc

    _RECEIVED.labels(kind=event.kind.value).inc()
    return {"status": "accepted", "event_id": event.event_id}


def _verify_signature(body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode("utf-8"), body, "sha256").hexdigest()
    # Constant-time compare.
    return hmac.compare_digest(expected, signature.lower())


def _error_response(status_code: int, *, code: ErrorCode, message: str) -> Any:
    envelope = ErrorEnvelope(
        error=ErrorBody(code=code, message=message),
        request_id=get_request_id(),
    )
    raise HTTPException(status_code=status_code, detail=envelope.model_dump(mode="json"))
