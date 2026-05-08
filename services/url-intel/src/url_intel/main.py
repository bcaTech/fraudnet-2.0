from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from fraudnet.obs import configure_logging, configure_tracing, get_logger
from url_intel.api import router
from url_intel.blocklist import Blocklist
from url_intel.settings import Settings
from url_intel.signals_listener import SignalsListener, make_settings_factory

_log = get_logger("url_intel.main")


def create_app(
    *,
    settings: Settings | None = None,
    blocklist: Blocklist | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()

    if blocklist is not None:
        # Test path — no Kafka, no Redis, just the API.
        app = FastAPI(title="url-intel", version="0.1.0", docs_url="/docs", redoc_url=None)
        app.state.blocklist = blocklist
        app.include_router(router)
        return app

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(service=settings.service_name, level=settings.log_level)
        configure_tracing(service=settings.service_name)

        bl = Blocklist(
            url=settings.redis_url,
            allow_list=settings.parse_allow_list(),
            signal_ttl_s=settings.signal_entry_ttl_s,
        )
        app.state.blocklist = bl

        listener_task: asyncio.Task[None] | None = None
        listener: SignalsListener | None = None
        if settings.enable_signals_listener:
            listener = SignalsListener(
                blocklist=bl,
                ttl_s=settings.signal_entry_ttl_s,
                kafka_settings_factory=make_settings_factory(
                    bootstrap=settings.kafka_bootstrap_servers,
                    schema_registry_url=settings.schema_registry_url,
                    group_id=settings.consumer_group,
                ),
            )
            listener_task = asyncio.create_task(listener.start(), name="url-intel-signals")

        _log.info("url_intel.started", env=settings.env)
        try:
            yield
        finally:
            _log.info("url_intel.stopping")
            if listener is not None:
                listener.stop()
            if listener_task is not None:
                listener_task.cancel()
            await bl.aclose()

    app = FastAPI(
        title="url-intel",
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
        "url_intel.main:app",
        host=settings.host,
        port=settings.port,
        log_config=None,
        reload=False,
    )


if __name__ == "__main__":
    run()
