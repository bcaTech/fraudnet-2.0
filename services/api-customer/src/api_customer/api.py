from __future__ import annotations

from time import time
from typing import Annotated, Any
from uuid import uuid4

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel

import jwt

from fraudnet.audit import record, with_purpose
from fraudnet.i18n import (
    parse_accept_language,
    raw_template,
    translate,
)
from fraudnet.kafka import AvroProducer
from fraudnet.obs import counter, get_logger, metrics_endpoint
from fraudnet.schemas.events import IntelEventV1
from fraudnet.schemas.types import EntityKind, MSISDN, Purpose
from api_customer.otp import OtpAdapter
from api_customer.session import SessionClaims, SessionTokenIssuer

_log = get_logger("api_customer.api")

_REPORTS = counter(
    "api_customer_reports_total",
    "Customer fraud reports submitted.",
    labelnames=("kind",),
)


router = APIRouter()


# --------------------------------------------------------------------------
# Dependencies
# --------------------------------------------------------------------------


def _otp(request: Request) -> OtpAdapter:
    return request.app.state.otp  # type: ignore[no-any-return]


def _session(request: Request) -> SessionTokenIssuer:
    return request.app.state.session  # type: ignore[no-any-return]


def _intel_producer(request: Request) -> AvroProducer[IntelEventV1]:
    return request.app.state.intel_producer  # type: ignore[no-any-return]


def _db_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pool  # type: ignore[no-any-return]


def _locale(
    accept_language: Annotated[str | None, Header(alias="Accept-Language")] = None,
) -> str:
    """Resolve a supported locale from the request's Accept-Language header.

    Falls back to English when the header is missing or names no
    supported locale. Persisted subscriber preference (in the profile)
    overrides this in the actuator path; this dep is for ad-hoc
    customer-facing API responses.
    """
    return parse_accept_language(accept_language)


async def _claims(
    request: Request,
    issuer: Annotated[SessionTokenIssuer, Depends(_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> SessionClaims:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return issuer.decode(token)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"invalid session: {exc}") from exc


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------


class RequestOtpBody(BaseModel):
    msisdn: str


class VerifyOtpBody(BaseModel):
    msisdn: str
    code: str


class SessionResponse(BaseModel):
    session_token: str
    expires_in: int


class ReportFraudBody(BaseModel):
    kind: str  # 'voice_scam' | 'smishing' | 'mule' | 'other'
    indicator_kind: str  # 'number' | 'wallet' | 'url'
    indicator: str
    notes: str | None = None


class BlockBody(BaseModel):
    msisdn: str


class AlertSummary(BaseModel):
    id: str
    type: str
    severity: str
    score: float
    status: str
    created_at: Any


# --------------------------------------------------------------------------
# Health / metrics
# --------------------------------------------------------------------------


@router.get("/health/live", include_in_schema=False)
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", include_in_schema=False)
async def readiness() -> dict[str, str]:
    return {"status": "ready"}


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    body, content_type = metrics_endpoint()()
    return PlainTextResponse(body, media_type=content_type)


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------


@router.post("/auth/request_otp", status_code=202)
async def request_otp(
    body: RequestOtpBody,
    otp: Annotated[OtpAdapter, Depends(_otp)],
) -> dict[str, str]:
    msisdn = MSISDN(body.msisdn)
    await otp.request(msisdn)
    # 202 for both delivered and not-delivered to avoid disclosing whether
    # the MSISDN is provisioned.
    return {"status": "otp_dispatched"}


@router.post("/auth/verify_otp", response_model=SessionResponse)
async def verify_otp(
    body: VerifyOtpBody,
    otp: Annotated[OtpAdapter, Depends(_otp)],
    issuer: Annotated[SessionTokenIssuer, Depends(_session)],
) -> SessionResponse:
    msisdn = MSISDN(body.msisdn)
    if not await otp.verify(msisdn, body.code):
        raise HTTPException(status_code=401, detail="invalid otp")
    token, ttl = issuer.issue(msisdn=msisdn)
    return SessionResponse(session_token=token, expires_in=ttl)


# --------------------------------------------------------------------------
# /me/* — authenticated customer endpoints
# --------------------------------------------------------------------------


@router.get("/me/alerts", response_model=list[AlertSummary])
async def list_my_alerts(
    claims: Annotated[SessionClaims, Depends(_claims)],
    pool: Annotated[asyncpg.Pool, Depends(_db_pool)],
) -> list[AlertSummary]:
    with with_purpose(Purpose.FRAUD_PREVENTION):
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, type, severity, score, status, created_at
                  FROM alerts
                 WHERE tenant_id = $1
                   AND subject_kind = 'number'
                   AND subject_id = $2
                 ORDER BY created_at DESC
                 LIMIT 50
                """,
                claims.tenant_id,
                claims.msisdn,
            )
        await record(
            action="customer.alerts.read",
            resource_kind="msisdn",
            resource_id=claims.msisdn,
            actor_kind="user",
            tenant_id=claims.tenant_id,
        )
        return [
            AlertSummary(
                id=str(r["id"]),
                type=r["type"],
                severity=r["severity"],
                score=float(r["score"]),
                status=r["status"],
                created_at=r["created_at"],
            )
            for r in rows
        ]


@router.post("/me/report")
async def submit_report(
    body: ReportFraudBody,
    claims: Annotated[SessionClaims, Depends(_claims)],
    intel: Annotated[AvroProducer[IntelEventV1], Depends(_intel_producer)],
) -> dict[str, str]:
    if body.indicator_kind not in {"number", "wallet", "url"}:
        raise HTTPException(status_code=400, detail="unsupported indicator_kind")

    indicator = body.indicator
    if body.indicator_kind == "number":
        indicator = MSISDN(indicator)

    now_ms = int(time() * 1000)
    event = IntelEventV1(
        event_id=f"int_{uuid4().hex[:24]}",
        event_ts_ms=now_ms,
        ingest_ts_ms=now_ms,
        source="api-customer",
        tenant_id=claims.tenant_id,
        kind="customer_report",
        indicator_kind=EntityKind(body.indicator_kind),
        indicator=indicator,
        confidence=0.6,  # customer reports are valuable but not authoritative
        attribution=f"customer:{claims.msisdn}",
        notes=body.notes,
    )
    await intel.send(event, key=indicator)
    _REPORTS.labels(kind=body.kind).inc()

    with with_purpose(Purpose.FRAUD_PREVENTION):
        await record(
            action="customer.report",
            resource_kind="indicator",
            resource_id=indicator,
            actor_kind="user",
            tenant_id=claims.tenant_id,
            metadata={"kind": body.kind, "indicator_kind": body.indicator_kind},
        )
    return {"status": "received", "event_id": event.event_id}


@router.post("/me/block")
async def request_block(
    body: BlockBody,
    claims: Annotated[SessionClaims, Depends(_claims)],
    intel: Annotated[AvroProducer[IntelEventV1], Depends(_intel_producer)],
) -> dict[str, str]:
    """Customer-initiated block of a number. Surfaces to the threat-intel
    pipeline; the fraud team reviews high-volume report patterns and may
    promote them to a Tier-1 block."""
    target = MSISDN(body.msisdn)
    now_ms = int(time() * 1000)
    event = IntelEventV1(
        event_id=f"int_{uuid4().hex[:24]}",
        event_ts_ms=now_ms,
        ingest_ts_ms=now_ms,
        source="api-customer:block",
        tenant_id=claims.tenant_id,
        kind="customer_report",
        indicator_kind=EntityKind.NUMBER,
        indicator=target,
        confidence=0.5,
        attribution=f"customer:{claims.msisdn}:block_request",
        notes="customer self-service block request",
    )
    await intel.send(event, key=target)

    with with_purpose(Purpose.FRAUD_PREVENTION):
        await record(
            action="customer.block_request",
            resource_kind="msisdn",
            resource_id=target,
            actor_kind="user",
            tenant_id=claims.tenant_id,
        )
    return {"status": "received"}


@router.get("/i18n/messages")
async def i18n_messages(locale: Annotated[str, Depends(_locale)]) -> dict[str, str]:
    """Bulk-translate the public message-key set in the negotiated locale.

    Used by the SMS template renderer + the customer self-service web UI
    to fetch all strings in one round-trip. The returned map preserves
    `{variable}` tokens unrendered — the caller substitutes at delivery
    time.
    """
    keys = (
        "spam_call_warning",
        "spam_sms_warning",
        "otp_fraud_warning",
        "url_blocked",
        "fraud_alert",
        "send_with_care_prompt",
        "return_to_sender_confirm",
        "diky_step1",
        "diky_step2",
        "diky_step3",
        "ask_me_first_prompt",
        "verified_business_badge",
        "passive_protection_enrolled",
        "transaction_held",
        "block_confirmed",
    )
    out: dict[str, str] = {"_locale": locale}
    for k in keys:
        out[k] = raw_template(k, locale=locale)
    return out


@router.get("/me/status")
async def my_status(
    claims: Annotated[SessionClaims, Depends(_claims)],
    pool: Annotated[asyncpg.Pool, Depends(_db_pool)],
    locale: Annotated[str, Depends(_locale)],
) -> dict[str, Any]:
    with with_purpose(Purpose.FRAUD_PREVENTION):
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    count(*) FILTER (WHERE status NOT IN ('closed', 'fp')) AS open_alerts,
                    count(*) FILTER (WHERE created_at > now() - interval '24 hours') AS recent_alerts,
                    max(severity) AS max_severity
                  FROM alerts
                 WHERE tenant_id = $1 AND subject_kind = 'number' AND subject_id = $2
                """,
                claims.tenant_id,
                claims.msisdn,
            )
    open_alerts = int(row["open_alerts"]) if row else 0
    return {
        "msisdn": claims.msisdn,
        "open_alerts": open_alerts,
        "recent_alerts": int(row["recent_alerts"]) if row else 0,
        "max_severity": row["max_severity"] if row else None,
        "locale": locale,
        "banner": (
            translate("fraud_alert", locale=locale)
            if open_alerts > 0
            else translate("passive_protection_enrolled", locale=locale)
        ),
    }
