from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.asyncio import Redis  # type: ignore[import-untyped]

from fraudnet.auth.principal import Principal
from fraudnet.auth.token import TokenValidator, TokenValidatorConfig, extract_principal
from fraudnet.federation import FederationClient
from fraudnet.federation.client import parse_peers
from fraudnet.graph import GraphClient
from fraudnet.kafka import AvroProducer, KafkaSettings
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
from fraudnet.schemas.events import IntelEventV1
from api_enterprise.api import router
from api_enterprise.db import (
    BlockRequestRepo,
    Database,
    EnterpriseAlertRepo,
    GroupAnalyticsRepo,
    SharedFlagRepo,
    TenantRepo,
)
from api_enterprise.rate_limit import (
    InMemoryRateLimiter,
    RateLimitConfig,
    RateLimiter,
    RedisRateLimiter,
)
from api_enterprise.settings import Settings

_log = get_logger("api_enterprise.main")


def create_app(
    *,
    settings: Settings | None = None,
    db: Database | None = None,
    graph: GraphClient | None = None,
    rate_limiter: RateLimiter | None = None,
    intel_producer: object | None = None,
    federation: FederationClient | None = None,
    token_validator: TokenValidator | None = None,
    test_principal: Principal | None = None,
) -> FastAPI:
    """Build the FastAPI app.

    Test path: pass `db`, optionally `graph`, `rate_limiter`, `intel_producer`,
    and either `token_validator` or `test_principal` (bypass auth).
    """
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(service=settings.service_name, level=settings.log_level)
        configure_tracing(service=settings.service_name)

        nonlocal db, graph, rate_limiter, intel_producer, token_validator, federation
        if db is None:
            db = Database(settings.postgres_dsn)
            await db.connect()
        if graph is None:
            graph = GraphClient(
                bolt_url=settings.memgraph_url,
                auth=(settings.memgraph_user, settings.memgraph_password)
                if settings.memgraph_user
                else None,
            )
        if rate_limiter is None:
            try:
                redis = Redis.from_url(settings.redis_url)
                await redis.ping()
                rate_limiter = RedisRateLimiter(
                    redis=redis,
                    config=RateLimitConfig(
                        capacity=settings.rate_limit_capacity,
                        refill_per_s=settings.rate_limit_refill_per_s,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "api_enterprise.rate_limit.fallback_in_memory",
                    error=str(exc),
                )
                rate_limiter = InMemoryRateLimiter(
                    config=RateLimitConfig(
                        capacity=settings.rate_limit_capacity,
                        refill_per_s=settings.rate_limit_refill_per_s,
                    )
                )
        if intel_producer is None:
            kafka_settings = KafkaSettings(
                bootstrap_servers=settings.kafka_bootstrap_servers,
                schema_registry_url=settings.schema_registry_url,
                client_id=settings.service_name,
            )
            ip: AvroProducer[IntelEventV1] = AvroProducer(
                settings=kafka_settings,
                model_cls=IntelEventV1,
            )
            await ip.start()
            intel_producer = ip
        if token_validator is None and test_principal is None:
            token_validator = TokenValidator(
                TokenValidatorConfig(
                    issuer=settings.jwt_issuer,
                    audience=settings.jwt_audience,
                    jwks_url=settings.jwks_url,
                )
            )
        if federation is None and settings.federation_peers:
            peers = parse_peers(
                settings.federation_peers,
                shared_secret=settings.federation_shared_secret,
            )
            if peers:
                federation = FederationClient(peers)
                _log.info(
                    "api_enterprise.federation.enabled",
                    peers=",".join(peers),
                )

        app.state.db = db
        app.state.alerts = EnterpriseAlertRepo(db)
        app.state.shared = SharedFlagRepo(db)
        app.state.blocks = BlockRequestRepo(db)
        app.state.tenants = TenantRepo(db)
        app.state.group = GroupAnalyticsRepo(db)
        app.state.graph = graph
        app.state.rate_limiter = rate_limiter
        app.state.intel_producer = intel_producer
        app.state.federation = federation
        app.state.token_validator = token_validator
        _log.info("api_enterprise.started", env=settings.env, port=settings.port)
        try:
            yield
        finally:
            _log.info("api_enterprise.stopping")
            if intel_producer is not None and hasattr(intel_producer, "stop"):
                await intel_producer.stop()  # type: ignore[func-returns-value]
            if federation is not None:
                await federation.close()
            if graph is not None:
                await graph.close()
            if db is not None:
                await db.close()

    app = FastAPI(
        title="api-enterprise",
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

        if test_principal is not None:
            request.state.principal = test_principal
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content=ErrorEnvelope(
                    error=ErrorBody(
                        code=ErrorCode.AUTH_REQUIRED,
                        message="missing bearer token",
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
                        code=ErrorCode.AUTH_INVALID_TOKEN,
                        message="invalid token",
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
        "api_enterprise.main:app",
        host=settings.host,
        port=settings.port,
        log_config=None,
        reload=False,
    )


if __name__ == "__main__":
    run()
