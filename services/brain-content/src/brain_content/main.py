from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from fraudnet.kafka import AvroProducer, KafkaSettings
from fraudnet.obs import configure_logging, configure_tracing, get_logger
from fraudnet.schemas.signals import SignalEventV1
from brain_content.api import router
from brain_content.classifier import ContentClassifier, HeuristicContentClassifier
from brain_content.runner import ContentRunner, make_settings_factory
from brain_content.settings import Settings
from brain_content.url_reputation import StaticBlocklist

_log = get_logger("brain_content.main")


def _build_classifier(settings: Settings) -> ContentClassifier:
    blocklist = StaticBlocklist(bad_domains=settings.parse_list("bad_domains"))
    return HeuristicContentClassifier(
        url_reputation=blocklist,
        bad_template_hashes=settings.parse_list("bad_url_template_hashes"),
        bad_body_hashes=settings.parse_list("bad_body_hashes"),
    )


def create_app(
    *,
    settings: Settings | None = None,
    classifier: ContentClassifier | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()

    if classifier is not None:
        # Test path
        app = FastAPI(title="brain-content", version="0.1.0", docs_url="/docs", redoc_url=None)
        app.state.classifier = classifier
        app.include_router(router)
        return app

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(service=settings.service_name, level=settings.log_level)
        configure_tracing(service=settings.service_name)
        classifier_inst = _build_classifier(settings)
        kafka_settings = KafkaSettings(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            schema_registry_url=settings.schema_registry_url,
            client_id=settings.service_name,
        )
        producer: AvroProducer[SignalEventV1] = AvroProducer(
            settings=kafka_settings,
            model_cls=SignalEventV1,
        )
        await producer.start()
        runner = ContentRunner(
            classifier=classifier_inst,
            signal_producer=producer,
            kafka_settings_factory=make_settings_factory(
                bootstrap=settings.kafka_bootstrap_servers,
                schema_registry_url=settings.schema_registry_url,
                group_id=settings.consumer_group,
            ),
        )
        app.state.classifier = classifier_inst
        runner_task = asyncio.create_task(runner.start(), name="content-runner")
        _log.info("brain_content.started", env=settings.env)
        try:
            yield
        finally:
            _log.info("brain_content.stopping")
            await runner.stop()
            runner_task.cancel()

    app = FastAPI(
        title="brain-content",
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
        "brain_content.main:app",
        host=settings.host,
        port=settings.port,
        log_config=None,
        reload=False,
    )


if __name__ == "__main__":
    run()
