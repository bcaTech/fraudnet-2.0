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
from brain_content.ml_classifier import TfidfLrClassifier
from brain_content.dns_scanner import DnsScanner
from brain_content.dns_scanner import make_settings_factory as make_dns_settings_factory
from brain_content.runner import ContentRunner, make_settings_factory
from brain_content.settings import Settings
from brain_content.url_reputation import StaticBlocklist

_log = get_logger("brain_content.main")


def _build_classifier(settings: Settings) -> ContentClassifier:
    blocklist = StaticBlocklist(bad_domains=settings.parse_list("bad_domains"))
    heuristic = HeuristicContentClassifier(
        url_reputation=blocklist,
        bad_template_hashes=settings.parse_list("bad_url_template_hashes"),
        bad_body_hashes=settings.parse_list("bad_body_hashes"),
    )
    if not settings.use_model_registry:
        return heuristic
    try:
        from fraudnet.registry import ModelRegistry

        registry = ModelRegistry(
            endpoint_url=settings.model_registry_endpoint,
            bucket=settings.model_registry_bucket,
            access_key=settings.model_registry_access_key,
            secret_key=settings.model_registry_secret_key,
        )
        return TfidfLrClassifier.load_from_registry(registry, heuristic=heuristic)
    except Exception as exc:  # noqa: BLE001
        _log.warning("brain_content.registry_unavailable", error=str(exc))
        return heuristic


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

        dns_scanner: DnsScanner | None = None
        dns_task: asyncio.Task[None] | None = None
        if settings.url_intel_url:
            dns_scanner = DnsScanner(
                url_intel_url=settings.url_intel_url,
                signal_producer=producer,
                kafka_settings_factory=make_dns_settings_factory(
                    bootstrap=settings.kafka_bootstrap_servers,
                    schema_registry_url=settings.schema_registry_url,
                    group_id=f"{settings.consumer_group}-dns",
                ),
                timeout_s=settings.dns_scanner_timeout_s,
            )
            dns_task = asyncio.create_task(dns_scanner.start(), name="content-dns-scanner")

        _log.info("brain_content.started", env=settings.env, dns_scanner=bool(dns_scanner))
        try:
            yield
        finally:
            _log.info("brain_content.stopping")
            await runner.stop()
            runner_task.cancel()
            if dns_scanner is not None:
                await dns_scanner.stop()
            if dns_task is not None:
                dns_task.cancel()

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
