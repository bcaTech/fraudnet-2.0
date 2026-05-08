from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from fraudnet.obs import configure_logging, configure_tracing, get_logger
from compliance.api import router
from compliance.runner import ComplianceRunner, make_settings_factory
from compliance.settings import Settings
from compliance.store import AuditStore

_log = get_logger("compliance.main")


def create_app(
    *,
    settings: Settings | None = None,
    store: AuditStore | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(service=settings.service_name, level=settings.log_level)
        configure_tracing(service=settings.service_name)
        nonlocal store
        if store is None:
            store = AuditStore(settings.audit_postgres_dsn)
            await store.connect()

        runner = ComplianceRunner(
            store=store,
            kafka_settings_factory=make_settings_factory(
                bootstrap=settings.kafka_bootstrap_servers,
                schema_registry_url=settings.schema_registry_url,
                group_id=settings.consumer_group,
            ),
        )
        app.state.store = store
        app.state.runner = runner
        runner_task = asyncio.create_task(runner.start(), name="compliance-runner")
        _log.info("compliance.started", env=settings.env)
        try:
            yield
        finally:
            _log.info("compliance.stopping")
            await runner.stop()
            runner_task.cancel()

    app = FastAPI(
        title="compliance",
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
        "compliance.main:app",
        host=settings.host,
        port=settings.port,
        log_config=None,
        reload=False,
    )


if __name__ == "__main__":
    run()
