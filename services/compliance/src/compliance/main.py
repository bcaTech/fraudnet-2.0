from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from fraudnet.obs import configure_logging, configure_tracing, get_logger
from compliance.api import router
from compliance.archive import ArchiveScheduler, IcebergArchiver, settings_from_env
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
        # JobStore for regulator-export endpoints.
        from compliance.regulators.jobs import JobStore as _JobStore

        app.state.job_store = _JobStore()

        archive_cfg = settings_from_env()
        archive_scheduler: ArchiveScheduler | None = None
        archive_task: asyncio.Task[None] | None = None
        if archive_cfg["enabled"]:
            try:
                archiver = IcebergArchiver(
                    pool=store.pool,
                    bucket=archive_cfg["bucket"],
                    endpoint_url=archive_cfg["endpoint_url"],
                    access_key=archive_cfg["access_key"],
                    secret_key=archive_cfg["secret_key"],
                )
                archive_scheduler = ArchiveScheduler(
                    archiver=archiver,
                    retention_days=archive_cfg["retention_days"],
                    interval_s=archive_cfg["interval_s"],
                )
                app.state.archiver = archiver
                app.state.archive_scheduler = archive_scheduler
                archive_task = asyncio.create_task(
                    archive_scheduler.start(), name="compliance-archive"
                )
                _log.info(
                    "compliance.archive_enabled",
                    retention_days=archive_cfg["retention_days"],
                    interval_s=archive_cfg["interval_s"],
                )
            except Exception as exc:  # noqa: BLE001
                _log.warning("compliance.archive_init_failed", error=str(exc))

        runner_task = asyncio.create_task(runner.start(), name="compliance-runner")
        _log.info("compliance.started", env=settings.env)
        try:
            yield
        finally:
            _log.info("compliance.stopping")
            if archive_scheduler is not None:
                await archive_scheduler.stop()
            if archive_task is not None:
                archive_task.cancel()
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
