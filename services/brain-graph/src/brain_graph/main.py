from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from fraudnet.federation import FederationClient
from fraudnet.federation.client import parse_peers
from fraudnet.graph import GraphClient
from fraudnet.kafka import AvroProducer, KafkaSettings
from fraudnet.obs import configure_logging, configure_tracing, get_logger
from fraudnet.schemas.events import MotifDetectedV1

from brain_graph.analyzer import Analyzer
from brain_graph.api import router
from brain_graph.runner import BatchScheduler
from brain_graph.settings import Settings

_log = get_logger("brain_graph.main")


def create_app(
    *,
    settings: Settings | None = None,
    analyzer: Analyzer | None = None,
    scheduler: BatchScheduler | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()

    if analyzer is not None:
        # Test path — caller injects a pre-wired analyzer.
        app = FastAPI(
            title="brain-graph",
            version="0.1.0",
            docs_url="/docs",
            redoc_url=None,
        )
        app.state.analyzer = analyzer
        app.state.scheduler = scheduler
        app.include_router(router)
        return app

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(service=settings.service_name, level=settings.log_level)
        configure_tracing(service=settings.service_name)

        graph = GraphClient(
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
        motif_producer: AvroProducer[MotifDetectedV1] = AvroProducer(
            settings=kafka_settings,
            model_cls=MotifDetectedV1,
        )
        await motif_producer.start()

        federation: FederationClient | None = None
        if settings.federation_peers:
            peers = parse_peers(
                settings.federation_peers,
                shared_secret=settings.federation_shared_secret,
            )
            if peers:
                federation = FederationClient(peers)
                _log.info(
                    "brain_graph.federation.enabled", peers=",".join(peers)
                )

        analyzer_inst = Analyzer(
            graph_client=graph,
            motif_producer=motif_producer,
            window_hours=settings.extract_window_hours,
            max_nodes=settings.extract_max_nodes,
            federation=federation,
        )
        sched = BatchScheduler(analyzer=analyzer_inst, interval_s=settings.batch_interval_s)
        app.state.analyzer = analyzer_inst
        app.state.scheduler = sched
        sched_task = asyncio.create_task(sched.start(), name="brain-graph-scheduler")
        _log.info("brain_graph.started", env=settings.env, interval_s=settings.batch_interval_s)
        try:
            yield
        finally:
            _log.info("brain_graph.stopping")
            await sched.stop()
            sched_task.cancel()
            await motif_producer.stop()
            if federation is not None:
                await federation.close()
            await graph.close()

    app = FastAPI(
        title="brain-graph",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )
    app.include_router(router)
    return app


def __getattr__(name: str) -> object:
    if name == "app":
        return create_app()
    raise AttributeError(name)


def run() -> None:
    import uvicorn

    settings = Settings.from_env()
    uvicorn.run(
        "brain_graph.main:app",
        host=settings.host,
        port=settings.port,
        log_config=None,
        reload=False,
    )


if __name__ == "__main__":
    run()
