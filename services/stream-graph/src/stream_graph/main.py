from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, Response

from fraudnet.graph import GraphClient, GraphScope
from fraudnet.kafka import AvroProducer, KafkaSettings
from fraudnet.obs import configure_logging, configure_tracing, get_logger, metrics_endpoint
from fraudnet.schemas.events import GraphMutationV1
from stream_graph.runner import GraphRunner, make_settings_factory
from stream_graph.settings import Settings

_log = get_logger("stream_graph.main")


def create_app(*, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(service=settings.service_name, level=settings.log_level)
        configure_tracing(service=settings.service_name)

        graph_client = GraphClient(
            bolt_url=settings.memgraph_url,
            auth=(settings.memgraph_user, settings.memgraph_password)
            if settings.memgraph_user
            else None,
        )
        kafka_settings = KafkaSettings(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            schema_registry_url=settings.schema_registry_url,
            client_id=settings.service_name,
        )
        producer: AvroProducer[GraphMutationV1] = AvroProducer(
            settings=kafka_settings,
            model_cls=GraphMutationV1,
        )
        await producer.start()

        runner = GraphRunner(
            graph_client=graph_client,
            scope=GraphScope(),
            producer=producer,
            kafka_settings_factory=make_settings_factory(
                bootstrap=settings.kafka_bootstrap_servers,
                schema_registry_url=settings.schema_registry_url,
                group_id=settings.consumer_group,
            ),
            graph_buffer_max=settings.graph_buffer_max,
            graph_flush_interval_s=settings.graph_flush_interval_s,
        )
        app.state.runner = runner
        runner_task = asyncio.create_task(runner.start(), name="graph-runner")
        _log.info("stream_graph.started", env=settings.env)
        try:
            yield
        finally:
            _log.info("stream_graph.stopping")
            await runner.stop()
            runner_task.cancel()

    app = FastAPI(
        title="stream-graph",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )

    @app.get("/health/live", include_in_schema=False)
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", include_in_schema=False)
    async def readiness() -> dict[str, str]:
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
        "stream_graph.main:app",
        host=settings.health_host,
        port=settings.health_port,
        log_config=None,
        reload=False,
    )


if __name__ == "__main__":
    run()
