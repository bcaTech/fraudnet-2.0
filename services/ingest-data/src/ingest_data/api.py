from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import PlainTextResponse, Response

from fraudnet.kafka.errors import DeliveryError
from fraudnet.obs import counter, get_logger, metrics_endpoint
from ingest_data.adapter import (
    DnsPushEvent,
    IpdrPushEvent,
    dns_to_canonical,
    ipdr_to_canonical,
    partition_key,
)
from ingest_data.deps import IngestDeps, deps_dependency

_log = get_logger("ingest_data.api")

_RECEIVED = counter(
    "ingest_data_received_total",
    "Data events received.",
    labelnames=("source", "kind"),
)
_REJECTED = counter(
    "ingest_data_rejected_total",
    "Data events rejected.",
    labelnames=("source", "reason"),
)
_UNATTRIBUTED = counter(
    "ingest_data_unattributed_total",
    "DNS events without subscriber attribution.",
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


@router.post("/dns/push", status_code=status.HTTP_202_ACCEPTED)
async def dns_push(
    request: Request,
    deps: Annotated[IngestDeps, Depends(deps_dependency)],
    x_dns_signature: Annotated[str | None, Header(alias="X-DNS-Signature")] = None,
) -> dict[str, str]:
    raw = await request.body()

    if deps.settings.dns_webhook_shared_secret:
        if not x_dns_signature or not _verify(raw, x_dns_signature, deps.settings.dns_webhook_shared_secret):
            _REJECTED.labels(source="dns", reason="bad_signature").inc()
            raise HTTPException(status_code=401, detail="invalid signature")

    try:
        push = DnsPushEvent.model_validate_json(raw)
    except Exception as exc:  # noqa: BLE001
        _REJECTED.labels(source="dns", reason="parse_error").inc()
        _log.warning("dns.parse_failed", error=str(exc))
        raise HTTPException(status_code=400, detail="invalid DNS payload") from exc

    try:
        event = dns_to_canonical(
            push,
            source=deps.settings.dns_resolver_id,
            resolver_id=deps.settings.dns_resolver_id,
        )
    except ValueError as exc:
        _REJECTED.labels(source="dns", reason="adapter_rejected").inc()
        _log.warning("dns.adapter_rejected", error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if event.msisdn is None:
        _UNATTRIBUTED.inc()

    if not await deps.idempotency.claim(event.event_id, ttl_s=deps.settings.idempotency_ttl_s):
        return {"status": "duplicate", "event_id": event.event_id}

    try:
        await deps.producer.send(event, key=partition_key(event))
    except DeliveryError as exc:
        _REJECTED.labels(source="dns", reason="kafka_delivery_failed").inc()
        _log.error("dns.kafka_delivery_failed", error=str(exc))
        raise HTTPException(status_code=503, detail="kafka delivery failed") from exc

    _RECEIVED.labels(source="dns", kind=event.kind).inc()
    return {"status": "accepted", "event_id": event.event_id}


@router.post("/ipdr/push", status_code=status.HTTP_202_ACCEPTED)
async def ipdr_push(
    request: Request,
    deps: Annotated[IngestDeps, Depends(deps_dependency)],
    x_ipdr_signature: Annotated[str | None, Header(alias="X-IPDR-Signature")] = None,
) -> dict[str, str]:
    raw = await request.body()

    if deps.settings.ipdr_webhook_shared_secret:
        if not x_ipdr_signature or not _verify(raw, x_ipdr_signature, deps.settings.ipdr_webhook_shared_secret):
            _REJECTED.labels(source="ipdr", reason="bad_signature").inc()
            raise HTTPException(status_code=401, detail="invalid signature")

    try:
        push = IpdrPushEvent.model_validate_json(raw)
    except Exception as exc:  # noqa: BLE001
        _REJECTED.labels(source="ipdr", reason="parse_error").inc()
        _log.warning("ipdr.parse_failed", error=str(exc))
        raise HTTPException(status_code=400, detail="invalid IPDR payload") from exc

    try:
        event = ipdr_to_canonical(
            push,
            source=deps.settings.ipdr_collector_id,
            collector_id=deps.settings.ipdr_collector_id,
        )
    except ValueError as exc:
        _REJECTED.labels(source="ipdr", reason="adapter_rejected").inc()
        _log.warning("ipdr.adapter_rejected", error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not await deps.idempotency.claim(event.event_id, ttl_s=deps.settings.idempotency_ttl_s):
        return {"status": "duplicate", "event_id": event.event_id}

    try:
        await deps.producer.send(event, key=partition_key(event))
    except DeliveryError as exc:
        _REJECTED.labels(source="ipdr", reason="kafka_delivery_failed").inc()
        _log.error("ipdr.kafka_delivery_failed", error=str(exc))
        raise HTTPException(status_code=503, detail="kafka delivery failed") from exc

    _RECEIVED.labels(source="ipdr", kind=event.kind).inc()
    return {"status": "accepted", "event_id": event.event_id}


def _verify(body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode("utf-8"), body, "sha256").hexdigest()
    return hmac.compare_digest(expected, signature.lower())
