"""HTTP API for url-intel.

Routes:
  GET  /health/{live,ready}
  GET  /metrics
  GET  /blocklist/check?url=...        — sub-5ms verdict
  GET  /blocklist/export               — full domain list (DNS sinkhole pull)
  POST /blocklist/add                  — manual analyst add
  POST /blocklist/remove               — manual analyst remove
  POST /feeds/import                   — bulk import from a threat-feed source
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

from fraudnet.obs import counter, get_logger, metrics_endpoint
from url_intel.blocklist import Blocklist

_log = get_logger("url_intel.api")


_CHECKS = counter(
    "url_intel_checks_total",
    "Blocklist checks served.",
    labelnames=("blocked", "allow_listed"),
)
_FEED_IMPORTS = counter(
    "url_intel_feed_imports_total",
    "Threat-feed import operations.",
    labelnames=("feed", "outcome"),
)
_MANUAL_OPS = counter(
    "url_intel_manual_ops_total",
    "Manual analyst operations.",
    labelnames=("op", "outcome"),
)


def _blocklist(request: Request) -> Blocklist:
    bl = getattr(request.app.state, "blocklist", None)
    if bl is None:
        raise RuntimeError("url-intel blocklist not initialised")
    return bl


router = APIRouter()


@router.get("/health/live", include_in_schema=False)
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", include_in_schema=False)
async def readiness(b: Annotated[Blocklist, Depends(_blocklist)]) -> dict[str, str]:
    return {"status": "ready" if b is not None else "starting"}


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    body, content_type = metrics_endpoint()()
    return PlainTextResponse(body, media_type=content_type)


# ----- check ---------------------------------------------------------------


class CheckResponse(BaseModel):
    blocked: bool
    domain: str
    matched: str | None = None
    allow_listed: bool = False
    source: str | None = None
    category: str | None = None
    confidence: float | None = None


@router.get("/blocklist/check", response_model=CheckResponse)
async def check(
    url: Annotated[str, Query(min_length=1, max_length=2048)],
    blocklist: Annotated[Blocklist, Depends(_blocklist)],
) -> CheckResponse:
    result = await blocklist.check(url)
    _CHECKS.labels(
        blocked=str(result.blocked).lower(),
        allow_listed=str(result.allow_listed).lower(),
    ).inc()
    return CheckResponse(
        blocked=result.blocked,
        domain=result.domain,
        matched=result.matched,
        allow_listed=result.allow_listed,
        source=result.entry.source if result.entry else None,
        category=result.entry.category if result.entry else None,
        confidence=result.entry.confidence if result.entry else None,
    )


# ----- export --------------------------------------------------------------


class ExportResponse(BaseModel):
    domains: list[str]
    count: int


@router.get("/blocklist/export", response_model=ExportResponse)
async def export(blocklist: Annotated[Blocklist, Depends(_blocklist)]) -> ExportResponse:
    domains = await blocklist.export_all()
    return ExportResponse(domains=domains, count=len(domains))


# ----- manual add / remove -------------------------------------------------


class AddRequest(BaseModel):
    domain: str = Field(min_length=1, max_length=512)
    source: str = "manual"
    category: Literal["phishing", "malware", "scam", "smishing", "unknown"] = "unknown"
    confidence: float = Field(default=0.95, ge=0.0, le=1.0)
    ttl_s: int | None = None


class AddResponse(BaseModel):
    added: bool
    domain: str
    reason: str


@router.post("/blocklist/add", response_model=AddResponse)
async def add(
    body: AddRequest,
    blocklist: Annotated[Blocklist, Depends(_blocklist)],
) -> AddResponse:
    added, reason = await blocklist.add(
        domain=body.domain,
        source=body.source,
        category=body.category,
        confidence=body.confidence,
        ttl_s=body.ttl_s,
    )
    _MANUAL_OPS.labels(op="add", outcome=reason).inc()
    return AddResponse(added=added, domain=body.domain, reason=reason)


class RemoveRequest(BaseModel):
    domain: str = Field(min_length=1, max_length=512)


class RemoveResponse(BaseModel):
    removed: bool
    domain: str


@router.post("/blocklist/remove", response_model=RemoveResponse)
async def remove(
    body: RemoveRequest,
    blocklist: Annotated[Blocklist, Depends(_blocklist)],
) -> RemoveResponse:
    removed = await blocklist.remove(body.domain)
    _MANUAL_OPS.labels(op="remove", outcome="removed" if removed else "absent").inc()
    return RemoveResponse(removed=removed, domain=body.domain)


# ----- threat-feed import --------------------------------------------------


class FeedEntry(BaseModel):
    domain: str = Field(min_length=1, max_length=512)
    category: Literal["phishing", "malware", "scam", "smishing", "unknown"] = "unknown"
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)


class FeedImportRequest(BaseModel):
    feed: str = Field(min_length=1, max_length=64)  # 'virustotal' | 'phishtank' | 'gsma' | ...
    entries: list[FeedEntry]
    ttl_s: int | None = None


class FeedImportResponse(BaseModel):
    feed: str
    submitted: int
    added: int
    rejected_invalid: int
    rejected_allow_listed: int


@router.post("/feeds/import", response_model=FeedImportResponse)
async def feeds_import(
    body: FeedImportRequest,
    blocklist: Annotated[Blocklist, Depends(_blocklist)],
) -> FeedImportResponse:
    if not body.entries:
        raise HTTPException(status_code=400, detail="no entries")
    added = invalid = allow_listed = 0
    source = f"feed:{body.feed}"
    for e in body.entries:
        ok, reason = await blocklist.add(
            domain=e.domain,
            source=source,
            category=e.category,
            confidence=e.confidence,
            ttl_s=body.ttl_s,
        )
        if ok:
            added += 1
        elif reason == "allow_listed":
            allow_listed += 1
        else:
            invalid += 1
    outcome = "ok" if added > 0 else "noop"
    _FEED_IMPORTS.labels(feed=body.feed, outcome=outcome).inc()
    _log.info(
        "url_intel.feed_import",
        feed=body.feed,
        submitted=len(body.entries),
        added=added,
        rejected_invalid=invalid,
        rejected_allow_listed=allow_listed,
    )
    return FeedImportResponse(
        feed=body.feed,
        submitted=len(body.entries),
        added=added,
        rejected_invalid=invalid,
        rejected_allow_listed=allow_listed,
    )
