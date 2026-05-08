"""Synchronous REST scoring endpoints.

Used by api-noc for ad-hoc scoring and (rarely) by decisions when a Tier-1
flow needs a fresh score and cannot tolerate the async signal lag.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel

from fraudnet.features import FeatureStore
from fraudnet.obs import get_logger, metrics_endpoint
from brain_behavioural.scorer import Scorer

_log = get_logger("brain_behavioural.api")


class _Deps(BaseModel):
    """Injected via app.state."""

    model_config = {"arbitrary_types_allowed": True}


def _deps(request: Request) -> dict[str, object]:
    if not hasattr(request.app.state, "scorer"):
        raise RuntimeError("brain-behavioural deps not initialised")
    return {
        "scorer": request.app.state.scorer,
        "store": request.app.state.feature_store,
    }


router = APIRouter()


@router.get("/health/live", include_in_schema=False)
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", include_in_schema=False)
async def readiness(deps: Annotated[dict[str, object], Depends(_deps)]) -> dict[str, str]:
    return {"status": "ready"} if deps["scorer"] else {"status": "starting"}  # type: ignore[truthy-bool]


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    body, content_type = metrics_endpoint()()
    return PlainTextResponse(body, media_type=content_type)


class ScoreNumberRequest(BaseModel):
    msisdn: str


class ScoreWalletRequest(BaseModel):
    wallet_id: str


class ScoreResponse(BaseModel):
    score: float
    signal_kind: str | None
    severity: str
    evidence: dict[str, str | int | float | bool]
    model_id: str
    model_version: str


@router.post("/score/number", response_model=ScoreResponse)
async def score_number(
    body: ScoreNumberRequest,
    deps: Annotated[dict[str, object], Depends(_deps)],
) -> ScoreResponse:
    store: FeatureStore = deps["store"]  # type: ignore[assignment]
    scorer: Scorer = deps["scorer"]  # type: ignore[assignment]
    features = await store.get_number(body.msisdn)
    if features is None:
        raise HTTPException(status_code=404, detail="no feature snapshot for msisdn")
    result = scorer.score_number(features)
    return ScoreResponse(
        score=result.score.value,
        signal_kind=result.signal_kind,
        severity=result.severity.value,
        evidence=result.evidence,
        model_id=result.score.model_id,
        model_version=result.score.model_version,
    )


@router.post("/score/wallet", response_model=ScoreResponse)
async def score_wallet(
    body: ScoreWalletRequest,
    deps: Annotated[dict[str, object], Depends(_deps)],
) -> ScoreResponse:
    store: FeatureStore = deps["store"]  # type: ignore[assignment]
    scorer: Scorer = deps["scorer"]  # type: ignore[assignment]
    features = await store.get_wallet(body.wallet_id)
    if features is None:
        raise HTTPException(status_code=404, detail="no feature snapshot for wallet")
    result = scorer.score_wallet(features)
    return ScoreResponse(
        score=result.score.value,
        signal_kind=result.signal_kind,
        severity=result.severity.value,
        evidence=result.evidence,
        model_id=result.score.model_id,
        model_version=result.score.model_version,
    )
