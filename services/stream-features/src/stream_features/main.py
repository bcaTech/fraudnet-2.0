"""stream-features entry point.

Brings up:
  - The FeaturePipeline (in-memory, per-key sliding-window state)
  - The FeatureRunner (one consumer per source topic, shared pipeline)
  - A small FastAPI side-car for /health and /metrics.

Phase 2 swaps the runner for a PyFlink job; the FeaturePipeline stays.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, Response

from fraudnet.features import AerospikeFeatureStore
from fraudnet.obs import configure_logging, configure_tracing, get_logger, metrics_endpoint
from stream_features.pipeline import FeaturePipeline
from stream_features.runner import FeatureRunner, make_settings_factory
from stream_features.settings import Settings

_log = get_logger("stream_features.main")


def _aerospike_hosts(spec: str) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for piece in spec.split(","):
        piece = piece.strip()
        if not piece:
            continue
        host, _, port = piece.partition(":")
        out.append((host, int(port or "3000")))
    return out


def create_app(*, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(service=settings.service_name, level=settings.log_level)
        configure_tracing(service=settings.service_name)
        pipeline = FeaturePipeline()
        store = AerospikeFeatureStore(hosts=_aerospike_hosts(settings.aerospike_hosts))
        runner = FeatureRunner(
            pipeline=pipeline,
            feature_store=store,
            kafka_settings_factory=make_settings_factory(
                bootstrap=settings.kafka_bootstrap_servers,
                schema_registry_url=settings.schema_registry_url,
                group_id=settings.consumer_group,
            ),
            feature_ttl_s=settings.feature_ttl_s,
        )
        app.state.pipeline = pipeline
        app.state.runner = runner
        runner_task = asyncio.create_task(runner.start(), name="feature-runner")
        _log.info("stream_features.started", env=settings.env)
        try:
            yield
        finally:
            _log.info("stream_features.stopping")
            await runner.stop()
            runner_task.cancel()

    app = FastAPI(
        title="stream-features",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )

    @app.get("/health/live", include_in_schema=False)
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", include_in_schema=False)
    async def readiness() -> dict[str, object]:
        runner = getattr(app.state, "runner", None)
        if runner is None:
            return {"status": "starting"}
        return {"status": "ready", "service": settings.service_name}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        body, content_type = metrics_endpoint()()
        return PlainTextResponse(body, media_type=content_type)

    return app


def __getattr__(name: str) -> object:
    if name == "app":
        return create_app()
    raise AttributeError(name)


def run() -> None:
    import uvicorn

    settings = Settings.from_env()
    uvicorn.run(
        "stream_features.main:app",
        host=settings.health_host,
        port=settings.health_port,
        log_config=None,
        reload=False,
    )


if __name__ == "__main__":
    run()
