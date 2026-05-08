from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, Response

from fraudnet.kafka import AvroProducer, KafkaSettings
from fraudnet.obs import configure_logging, configure_tracing, get_logger, metrics_endpoint
from fraudnet.schemas.signals import SignalEventV1
from brain_otp_guard.registry import RedisActiveCallRegistry, RedisSuppressionStore
from brain_otp_guard.runner import OtpGuardRunner, make_settings_factory
from brain_otp_guard.settings import Settings

_log = get_logger("brain_otp_guard.main")


def create_app(*, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(service=settings.service_name, level=settings.log_level)
        configure_tracing(service=settings.service_name)

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

        registry = RedisActiveCallRegistry(
            url=settings.redis_url, ttl_s=settings.active_call_ttl_s
        )
        suppression = RedisSuppressionStore(
            url=settings.redis_url, window_s=settings.suppression_window_s
        )

        runner = OtpGuardRunner(
            registry=registry,
            suppression=suppression,
            signal_producer=producer,
            bank_short_codes=settings.parse_bank_short_codes(),
            kafka_settings_factory=make_settings_factory(
                bootstrap=settings.kafka_bootstrap_servers,
                schema_registry_url=settings.schema_registry_url,
                group_id=settings.consumer_group,
            ),
        )
        app.state.runner = runner
        runner_task = asyncio.create_task(runner.start(), name="otp-guard-runner")
        _log.info("brain_otp_guard.started", env=settings.env)
        try:
            yield
        finally:
            _log.info("brain_otp_guard.stopping")
            await runner.stop()
            runner_task.cancel()

    app = FastAPI(
        title="brain-otp-guard",
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
        return {"status": "ready" if runner else "starting"}

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
        "brain_otp_guard.main:app",
        host=settings.host,
        port=settings.port,
        log_config=None,
        reload=False,
    )


if __name__ == "__main__":
    run()
