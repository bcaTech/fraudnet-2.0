from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.asyncio import Redis  # type: ignore[import-untyped]

from fraudnet.auth.principal import Principal
from fraudnet.auth.token import TokenValidator, TokenValidatorConfig, extract_principal
from fraudnet.kafka import KafkaSettings
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
from intel_repository.api import router
from intel_repository.cache import CachedIntelRepo
from intel_repository.populator import IntelPopulator
from intel_repository.repo import Database, IntelRepo
from intel_repository.settings import Settings

_log = get_logger("intel_repository.main")


def create_app(
    *,
    settings: Settings | None = None,
    db: Database | None = None,
    repo: IntelRepo | None = None,
    cache: CachedIntelRepo | None = None,
    populator: IntelPopulator | None = None,
    token_validator: TokenValidator | None = None,
    test_principal: Principal | None = None,
    skip_kafka: bool = False,
) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(service=settings.service_name, level=settings.log_level)
        configure_tracing(service=settings.service_name)
        nonlocal db, repo, cache, populator, token_validator
        if db is None:
            db = Database(settings.postgres_dsn)
            await db.connect()
        if repo is None:
            repo = IntelRepo(db)
        redis: Redis | None = None
        if cache is None:
            try:
                redis = Redis.from_url(settings.redis_url)
                await redis.ping()
            except Exception:  # noqa: BLE001
                redis = None
                _log.warning("intel_repository.redis_unavailable")
            cache = CachedIntelRepo(
                repo=repo, redis=redis, hit_ttl_s=settings.cache_ttl_s
            )
        if populator is None and not skip_kafka:

            def _factory(client_id: str) -> KafkaSettings:
                return KafkaSettings(
                    bootstrap_servers=settings.kafka_bootstrap_servers,
                    schema_registry_url=settings.schema_registry_url,
                    client_id=client_id,
                    group_id=client_id,
                )

            populator = IntelPopulator(
                settings=settings, repo=repo, kafka_settings_factory=_factory
            )
            asyncio.create_task(populator.start(), name="intel-populator")
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
        app.state.cache = cache
        app.state.populator = populator
        app.state.token_validator = token_validator
        _log.info("intel_repository.started", env=settings.env)
        try:
            yield
        finally:
            _log.info("intel_repository.stopping")
            if populator is not None:
                await populator.stop()
            if db is not None:
                await db.close()

    app = FastAPI(
        title="intel-repository",
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
            or path.startswith("/intel/lookup/")  # service-to-service hot path
        ):
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
        "intel_repository.main:app",
        host=settings.host,
        port=settings.port,
        log_config=None,
        reload=False,
    )


if __name__ == "__main__":
    run()
