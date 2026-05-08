"""Sync REST scoring for ad-hoc content classification."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel

from fraudnet.obs import get_logger, metrics_endpoint
from brain_content.classifier import ContentClassifier

_log = get_logger("brain_content.api")


def _classifier(request: Request) -> ContentClassifier:
    if not hasattr(request.app.state, "classifier"):
        raise RuntimeError("brain-content classifier not initialised")
    return request.app.state.classifier  # type: ignore[no-any-return]


router = APIRouter()


@router.get("/health/live", include_in_schema=False)
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", include_in_schema=False)
async def readiness(c: Annotated[ContentClassifier, Depends(_classifier)]) -> dict[str, str]:
    return {"status": "ready"} if c else {"status": "starting"}  # type: ignore[truthy-bool]


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    body, content_type = metrics_endpoint()()
    return PlainTextResponse(body, media_type=content_type)


class ScoreSmsRequest(BaseModel):
    body: str | None = None
    body_hash: str | None = None
    template_hash: str | None = None


class ScoreSmsResponse(BaseModel):
    score: float
    signal_kind: str | None
    severity: str
    evidence: dict[str, str | int | float | bool]
    matched_urls: list[str]
    model_id: str
    model_version: str


@router.post("/score/sms", response_model=ScoreSmsResponse)
async def score_sms(
    body: ScoreSmsRequest,
    classifier: Annotated[ContentClassifier, Depends(_classifier)],
) -> ScoreSmsResponse:
    result = classifier.classify(
        body=body.body,
        body_hash=body.body_hash,
        template_hash=body.template_hash,
    )
    return ScoreSmsResponse(
        score=result.score.value,
        signal_kind=result.signal_kind,
        severity=result.severity.value,
        evidence=result.evidence,
        matched_urls=list(result.matched_urls),
        model_id=result.score.model_id,
        model_version=result.score.model_version,
    )
