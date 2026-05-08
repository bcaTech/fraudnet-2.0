from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from fastapi import FastAPI

from fraudnet.obs import configure_logging, configure_tracing, get_logger
from business_registry.api import router
from business_registry.registry import RedisCache, Registry
from business_registry.settings import Settings

_log = get_logger("business_registry.main")


def create_app(*, settings: Settings | None = None, registry: Any | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    if registry is not None:
        app = FastAPI(title="business-registry", version="0.1.0", docs_url="/docs", redoc_url=None)
        app.state.registry = registry
        app.include_router(router)
        return app

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(service=settings.service_name, level=settings.log_level)
        configure_tracing(service=settings.service_name)

        pool = await asyncpg.create_pool(settings.database_url)
        cache = RedisCache(url=settings.redis_url, ttl_s=settings.cache_ttl_s)
        registry = Registry(pool=pool, cache=cache)
        app.state.pool = pool
        app.state.cache = cache
        app.state.registry = registry
        _log.info("business_registry.started", env=settings.env)
        try:
            yield
        finally:
            _log.info("business_registry.stopping")
            await cache.aclose()
            await pool.close()

    app = FastAPI(
        title="business-registry",
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
        "business_registry.main:app",
        host=settings.host,
        port=settings.port,
        log_config=None,
        reload=False,
    )


if __name__ == "__main__":
    run()
