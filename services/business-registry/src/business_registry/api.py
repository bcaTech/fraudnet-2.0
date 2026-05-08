"""HTTP API for business-registry.

Routes:
  POST   /businesses                         — register a business
  POST   /businesses/{id}/verify             — flip status to 'verified'
  POST   /businesses/{id}/msisdns            — add a verified MSISDN
  POST   /businesses/{id}/shortcodes         — add a verified short code
  GET    /businesses/{id}                    — business detail
  GET    /businesses                         — list (filterable by status)
  GET    /lookup/msisdn/{msisdn}             — verified-business lookup
  GET    /lookup/shortcode/{shortcode}       — verified-shortcode lookup
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

from fraudnet.obs import counter, get_logger, metrics_endpoint

_log = get_logger("business_registry.api")


_LOOKUPS = counter(
    "business_registry_lookups_total",
    "Lookups served.",
    labelnames=("kind", "matched", "verified"),
)
_OPS = counter(
    "business_registry_ops_total",
    "Mutating operations.",
    labelnames=("op",),
)


def _registry(request: Request):
    return request.app.state.registry


router = APIRouter()


# ---- health ---------------------------------------------------------------


@router.get("/health/live", include_in_schema=False)
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", include_in_schema=False)
async def readiness(request: Request) -> dict[str, str]:
    return {"status": "ready" if getattr(request.app.state, "registry", None) else "starting"}


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    body, content_type = metrics_endpoint()()
    return PlainTextResponse(body, media_type=content_type)


# ---- schemas --------------------------------------------------------------


class BusinessOut(BaseModel):
    id: str
    name: str
    registration_number: str | None = None
    status: str
    verified_at: str | None = None
    tenant_id: str


class CreateBusinessBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    registration_number: str | None = Field(default=None, max_length=64)


class AddMsisdnBody(BaseModel):
    msisdn: str = Field(min_length=4, max_length=20)
    kind: Literal["voice", "sms", "both"] = "both"


class AddShortcodeBody(BaseModel):
    shortcode: str = Field(min_length=1, max_length=64)


class LookupOut(BaseModel):
    matched: bool
    is_verified: bool
    business: BusinessOut | None = None


# ---- mutations ------------------------------------------------------------


@router.post("/businesses", response_model=BusinessOut, status_code=201)
async def create_business(
    body: CreateBusinessBody, registry: Annotated[Any, Depends(_registry)]
) -> BusinessOut:
    biz = await registry.create_business(
        name=body.name, registration_number=body.registration_number
    )
    _OPS.labels(op="create").inc()
    return BusinessOut(**biz.__dict__)


@router.post("/businesses/{business_id}/verify", response_model=BusinessOut)
async def verify_business(
    business_id: str, registry: Annotated[Any, Depends(_registry)]
) -> BusinessOut:
    try:
        biz = await registry.verify_business(business_id=business_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="business not found") from None
    _OPS.labels(op="verify").inc()
    return BusinessOut(**biz.__dict__)


@router.post("/businesses/{business_id}/msisdns", status_code=201)
async def add_msisdn(
    business_id: str,
    body: AddMsisdnBody,
    registry: Annotated[Any, Depends(_registry)],
) -> dict[str, str]:
    try:
        await registry.add_msisdn(business_id=business_id, msisdn=body.msisdn, kind=body.kind)
    except LookupError:
        raise HTTPException(status_code=404, detail="business not found") from None
    _OPS.labels(op="add_msisdn").inc()
    return {"status": "added", "msisdn": body.msisdn}


@router.post("/businesses/{business_id}/shortcodes", status_code=201)
async def add_shortcode(
    business_id: str,
    body: AddShortcodeBody,
    registry: Annotated[Any, Depends(_registry)],
) -> dict[str, str]:
    try:
        await registry.add_shortcode(business_id=business_id, shortcode=body.shortcode)
    except LookupError:
        raise HTTPException(status_code=404, detail="business not found") from None
    _OPS.labels(op="add_shortcode").inc()
    return {"status": "added", "shortcode": body.shortcode.upper()}


# ---- queries --------------------------------------------------------------


@router.get("/businesses", response_model=list[BusinessOut])
async def list_businesses(
    registry: Annotated[Any, Depends(_registry)],
    status: Annotated[str | None, Query()] = None,
) -> list[BusinessOut]:
    rows = await registry.list_businesses(status=status)
    return [BusinessOut(**b.__dict__) for b in rows]


@router.get("/businesses/{business_id}", response_model=BusinessOut)
async def get_business(
    business_id: str, registry: Annotated[Any, Depends(_registry)]
) -> BusinessOut:
    biz = await registry.get_business(business_id)
    if biz is None:
        raise HTTPException(status_code=404, detail="business not found")
    return BusinessOut(**biz.__dict__)


@router.get("/lookup/msisdn/{msisdn}", response_model=LookupOut)
async def lookup_msisdn(
    msisdn: str, registry: Annotated[Any, Depends(_registry)]
) -> LookupOut:
    result = await registry.lookup_msisdn(msisdn)
    _LOOKUPS.labels(
        kind="msisdn",
        matched=str(result.matched).lower(),
        verified=str(result.is_verified).lower(),
    ).inc()
    return LookupOut(
        matched=result.matched,
        is_verified=result.is_verified,
        business=BusinessOut(**result.business.__dict__) if result.business else None,
    )


@router.get("/lookup/shortcode/{shortcode}", response_model=LookupOut)
async def lookup_shortcode(
    shortcode: str, registry: Annotated[Any, Depends(_registry)]
) -> LookupOut:
    result = await registry.lookup_shortcode(shortcode)
    _LOOKUPS.labels(
        kind="shortcode",
        matched=str(result.matched).lower(),
        verified=str(result.is_verified).lower(),
    ).inc()
    return LookupOut(
        matched=result.matched,
        is_verified=result.is_verified,
        business=BusinessOut(**result.business.__dict__) if result.business else None,
    )
