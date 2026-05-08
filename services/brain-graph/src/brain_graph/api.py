"""REST API for brain-graph.

Mostly an on-demand trigger for the batch (so investigators can force a
run without waiting for the next 15-minute window) plus health/metrics.
The actual analysis output lands on motifs.detected.v1 and is consumed
by decisions; the API surfaces a summary of the most-recent run.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse, Response

from fraudnet.obs import get_logger, metrics_endpoint

from brain_graph.analyzer import AnalysisResult, Analyzer
from brain_graph.runner import BatchScheduler

_log = get_logger("brain_graph.api")


def _analyzer(request: Request) -> Analyzer:
    return request.app.state.analyzer  # type: ignore[no-any-return]


def _scheduler(request: Request) -> BatchScheduler | None:
    return getattr(request.app.state, "scheduler", None)


router = APIRouter()


@router.get("/health/live", include_in_schema=False)
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", include_in_schema=False)
async def readiness(analyzer: Annotated[Analyzer, Depends(_analyzer)]) -> dict[str, str]:
    return {"status": "ready"} if analyzer is not None else {"status": "starting"}


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    body, content_type = metrics_endpoint()()
    return PlainTextResponse(body, media_type=content_type)


@router.post("/analyze")
async def analyze_now(
    analyzer: Annotated[Analyzer, Depends(_analyzer)],
) -> dict[str, Any]:
    """On-demand batch trigger. Returns a summary of the run."""
    result = await analyzer.run_once()
    return _result_summary(result)


@router.post("/scheduler/trigger")
async def scheduler_trigger(
    request: Request,
) -> dict[str, str]:
    """Force the scheduler's next tick to run immediately. Idempotent."""
    sched = _scheduler(request)
    if sched is None:
        return {"status": "no_scheduler"}
    await sched.trigger()
    return {"status": "ok"}


def _result_summary(result: AnalysisResult) -> dict[str, Any]:
    return {
        "extracted_at_ms": result.extracted_at_ms,
        "node_count": result.node_count,
        "edge_count": result.edge_count,
        "motif_count": len(result.motifs),
        "motifs_by_type": {
            m: sum(1 for x in result.motifs if x.motif == m)
            for m in {x.motif for x in result.motifs}
        },
        "community_count": len(result.communities),
        "ring_count": len(result.rings),
        "rings": [
            {
                "id": r.id,
                "type": r.type,
                "members": list(r.members),
                "composite_score": r.composite_score,
                "member_count": r.member_count,
                "shared_device_count": r.shared_device_count,
                "shared_wallet_flow_count": r.shared_wallet_flow_count,
                "motif_count": r.motif_count,
            }
            for r in result.rings
        ],
        "cross_opco_count": len(result.cross_opco_rings),
        "cross_opco_rings": [
            {
                "local_ring_id": cor.ring.id,
                "composite_score": cor.composite_score,
                "exit_count": len(cor.exits),
                "confirmation_count": len(cor.confirmations),
                "peers": sorted({peer for peer, _ in cor.confirmations}),
                "members_hashed_count": len(cor.members_hashed),
            }
            for cor in result.cross_opco_rings
        ],
    }
