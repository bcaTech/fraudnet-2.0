"""ingest-momo entry point.

Run via:
    uvicorn ingest_momo.main:app --host 0.0.0.0 --port 8100
or, in the dev Procfile, `make dev SERVICE=ingest-momo`.

Tests build the app via `create_app(deps=...)` to skip the production
lifespan and avoid pulling Kafka / Redis up.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from fraudnet.obs import (
    bind_context,
    clear_context,
    configure_logging,
    configure_tracing,
    get_logger,
    new_request_id,
    set_request_id,
)
from fraudnet.schemas.errors import ErrorBody, ErrorCode, ErrorEnvelope, FraudNetError
from ingest_momo.api import router
from ingest_momo.deps import IngestDeps, build_deps, teardown_deps
from ingest_momo.settings import Settings

_log = get_logger("ingest_momo.main")


def create_app(*, deps: IngestDeps | None = None) -> FastAPI:
    """Build the FastAPI app.

    Args:
        deps: pre-built IngestDeps for tests. When provided the production
            lifespan is bypassed and deps are installed directly.
    """
    if deps is None:

        @asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncIterator[None]:
            settings = Settings.from_env()
            configure_logging(service=settings.service_name, level=settings.log_level)
            configure_tracing(service=settings.service_name)
            built = await build_deps(settings)
            app.state.deps = built
            _log.info("ingest_momo.started", env=settings.env)
            try:
                yield
            finally:
                _log.info("ingest_momo.stopping")
                await teardown_deps(built)

        app = FastAPI(
            title="ingest-momo",
            version="0.1.0",
            lifespan=lifespan,
            docs_url="/docs",
            redoc_url=None,
        )
    else:
        # Test path: deps are already built; the production lifespan is
        # skipped so the app does not require Kafka / Redis to start.
        app = FastAPI(title="ingest-momo", version="0.1.0", docs_url="/docs", redoc_url=None)
        app.state.deps = deps

    app.include_router(router)
    _wire_error_handlers(app)
    _wire_request_id_middleware(app)
    return app


def _wire_request_id_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def _request_id(request: Request, call_next):  # type: ignore[no-untyped-def]
        rid = request.headers.get("x-request-id") or new_request_id()
        set_request_id(rid)
        bind_context(request_id=rid)
        try:
            response = await call_next(request)
        finally:
            clear_context()
        response.headers["x-request-id"] = rid
        return response


def _wire_error_handlers(app: FastAPI) -> None:
    from fraudnet.obs import get_request_id

    @app.exception_handler(FraudNetError)
    async def _fraudnet_error(_request: Request, exc: FraudNetError) -> JSONResponse:
        envelope = exc.to_envelope(request_id=get_request_id())
        return JSONResponse(
            status_code=exc.http_status,
            content=envelope.model_dump(mode="json"),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        envelope = ErrorEnvelope(
            error=ErrorBody(
                code=ErrorCode.VALIDATION_FAILED,
                message="request validation failed",
                details={"errors": exc.errors()},
            ),
            request_id=get_request_id(),
        )
        return JSONResponse(status_code=400, content=envelope.model_dump(mode="json"))


# Lazy app construction for uvicorn — module import (e.g. for tests) does not
# trigger Settings.from_env() or any I/O. Uvicorn calls `ingest_momo.main:app`
# which Python resolves via __getattr__ here.
def __getattr__(name: str) -> object:
    if name == "app":
        return create_app()
    raise AttributeError(name)


def run() -> None:
    """Entry point for `python -m ingest_momo`."""
    import uvicorn

    settings = Settings.from_env()
    uvicorn.run(
        "ingest_momo.main:app",
        host=settings.host,
        port=settings.port,
        log_config=None,  # we configure via fraudnet.obs
        reload=False,
    )


if __name__ == "__main__":
    run()
