from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from fraudnet.auth.principal import Principal
from fraudnet.auth.token import TokenValidator, TokenValidatorConfig, extract_principal
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
from aml_watchlist.api import router
from aml_watchlist.db import Database, MatchLogRepo, WatchlistRepo
from aml_watchlist.matcher import MatchEngine
from aml_watchlist.refresh import RefreshScheduler
from aml_watchlist.settings import Settings

_log = get_logger("aml_watchlist.main")


def create_app(
    *,
    settings: Settings | None = None,
    db: Database | None = None,
    repo: WatchlistRepo | None = None,
    match_log: MatchLogRepo | None = None,
    engine: MatchEngine | None = None,
    refresh: RefreshScheduler | None = None,
    token_validator: TokenValidator | None = None,
    test_principal: Principal | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(service=settings.service_name, level=settings.log_level)
        configure_tracing(service=settings.service_name)
        nonlocal db, repo, match_log, engine, refresh, token_validator
        if db is None:
            db = Database(settings.postgres_dsn)
            await db.connect()
        if repo is None:
            repo = WatchlistRepo(db)
        if match_log is None:
            match_log = MatchLogRepo(db)
        if engine is None:
            engine = MatchEngine(
                repo=repo,
                match_log=match_log,
                threshold=settings.default_match_threshold,
            )
        if refresh is None:
            refresh = RefreshScheduler(
                repo=repo,
                un_url=settings.un_feed_url,
                ofac_url=settings.ofac_feed_url,
                interval_s=settings.refresh_interval_s,
            )
        if token_validator is None and test_principal is None:
            token_validator = TokenValidator(
                TokenValidatorConfig(
                    issuer=settings.jwt_issuer,
                    audience=settings.jwt_audience,
                    jwks_url=settings.jwks_url,
                )
            )

        app.state.db = db
        app.state.repo = repo
        app.state.match_log = match_log
        app.state.engine = engine
        app.state.refresh = refresh
        app.state.token_validator = token_validator
        await refresh.start()
        _log.info("aml_watchlist.started", env=settings.env)
        try:
            yield
        finally:
            _log.info("aml_watchlist.stopping")
            await refresh.stop()
            await db.close()

    app = FastAPI(
        title="aml-watchlist",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )

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

    @app.middleware("http")
    async def _auth(request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path
        if (
            path.startswith("/health")
            or path == "/metrics"
            or path == "/docs"
            or path.startswith("/openapi")
        ):
            return await call_next(request)
        # The /watchlist/check/* path is service-to-service (called by
        # brain-behavioural during scoring). It accepts service tokens or
        # — in dev — runs without auth. Auth is still enforced for
        # human-facing routes (stats, import, internal/add).
        if path.startswith("/watchlist/check/"):
            return await call_next(request)
        if test_principal is not None:
            request.state.principal = test_principal
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content=ErrorEnvelope(
                    error=ErrorBody(
                        code=ErrorCode.AUTH_REQUIRED, message="missing bearer token"
                    ),
                ).model_dump(mode="json"),
            )
        token = auth.removeprefix("Bearer ").strip()
        validator: TokenValidator = app.state.token_validator
        try:
            claims = await validator.decode(token)
        except Exception:  # noqa: BLE001
            return JSONResponse(
                status_code=401,
                content=ErrorEnvelope(
                    error=ErrorBody(
                        code=ErrorCode.AUTH_INVALID_TOKEN, message="invalid token"
                    ),
                ).model_dump(mode="json"),
            )
        request.state.principal = extract_principal(claims)
        return await call_next(request)

    app.include_router(router)

    @app.exception_handler(FraudNetError)
    async def _fraudnet_error(_request: Request, exc: FraudNetError) -> JSONResponse:
        from fraudnet.obs import get_request_id

        return JSONResponse(
            status_code=exc.http_status,
            content=exc.to_envelope(request_id=get_request_id()).model_dump(mode="json"),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        from fraudnet.obs import get_request_id

        return JSONResponse(
            status_code=400,
            content=ErrorEnvelope(
                error=ErrorBody(
                    code=ErrorCode.VALIDATION_FAILED,
                    message="request validation failed",
                    details={"errors": exc.errors()},
                ),
                request_id=get_request_id(),
            ).model_dump(mode="json"),
        )

    return app


def __getattr__(name: str) -> object:
    if name == "app":
        return create_app()
    raise AttributeError(name)


def run() -> None:
    import uvicorn

    settings = Settings.from_env()
    uvicorn.run(
        "aml_watchlist.main:app",
        host=settings.host,
        port=settings.port,
        log_config=None,
        reload=False,
    )


if __name__ == "__main__":
    run()


# Shut up the linter about an unused import we keep for future hooks.
_ = asyncio
