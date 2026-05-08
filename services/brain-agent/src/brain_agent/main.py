from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.asyncio import Redis  # type: ignore[import-untyped]

from fraudnet.auth.principal import Principal
from fraudnet.auth.token import TokenValidator, TokenValidatorConfig, extract_principal
from fraudnet.features import AerospikeFeatureStore, FeatureStore
from fraudnet.graph import GraphClient
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
from brain_agent.agent import InvestigationAgent, JobStore
from brain_agent.api import router
from brain_agent.llm import AnthropicLLMClient, LLMClient, StubLLMClient
from brain_agent.rate_limit import (
    InMemoryRateLimiter,
    RateLimitConfig,
    RateLimiter,
    RedisRateLimiter,
)
from brain_agent.settings import Settings

_log = get_logger("brain_agent.main")


def _build_llm(settings: Settings) -> LLMClient:
    if settings.anthropic_api_key:
        _log.info("brain_agent.llm.anthropic", model=settings.anthropic_model)
        return AnthropicLLMClient(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            max_tokens=settings.anthropic_max_tokens,
            timeout_s=settings.anthropic_timeout_s,
        )
    _log.info("brain_agent.llm.stub")
    return StubLLMClient()


def create_app(
    *,
    settings: Settings | None = None,
    pool: asyncpg.Pool | None = None,
    graph: GraphClient | None = None,
    features: FeatureStore | None = None,
    llm: LLMClient | None = None,
    rate_limiter: RateLimiter | None = None,
    job_store: JobStore | None = None,
    token_validator: TokenValidator | None = None,
    test_principal: Principal | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(service=settings.service_name, level=settings.log_level)
        configure_tracing(service=settings.service_name)

        nonlocal pool, graph, features, llm, rate_limiter, job_store, token_validator
        if pool is None:
            pool = await asyncpg.create_pool(
                settings.postgres_dsn, min_size=2, max_size=10
            )
        if graph is None:
            graph = GraphClient(
                bolt_url=settings.memgraph_url,
                auth=(settings.memgraph_user, settings.memgraph_password)
                if settings.memgraph_user
                else None,
            )
        if features is None:
            try:
                hosts = [
                    (h.split(":", 1)[0], int(h.split(":", 1)[1]))
                    for h in settings.aerospike_hosts.split(",")
                ]
                features = AerospikeFeatureStore(hosts=hosts)
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "brain_agent.aerospike_unavailable",
                    error=str(exc),
                )
                from fraudnet.features import InMemoryFeatureStore

                features = InMemoryFeatureStore()
        if llm is None:
            llm = _build_llm(settings)

        # Redis is shared across rate limiter and job store.
        redis: Redis | None = None
        try:
            redis = Redis.from_url(settings.redis_url)
            await redis.ping()
        except Exception:  # noqa: BLE001
            redis = None
            _log.warning("brain_agent.redis_unavailable")

        if rate_limiter is None:
            cfg = RateLimitConfig(
                capacity=settings.rate_limit_capacity,
                refill_per_s=settings.rate_limit_refill_per_s,
            )
            rate_limiter = (
                RedisRateLimiter(redis=redis, config=cfg)
                if redis is not None
                else InMemoryRateLimiter(config=cfg)
            )
        if job_store is None:
            job_store = JobStore(redis=redis)

        if token_validator is None and test_principal is None:
            token_validator = TokenValidator(
                TokenValidatorConfig(
                    issuer=settings.jwt_issuer,
                    audience=settings.jwt_audience,
                    jwks_url=settings.jwks_url,
                )
            )

        agent = InvestigationAgent(llm=llm, store=job_store)
        app.state.pool = pool
        app.state.graph = graph
        app.state.features = features
        app.state.agent = agent
        app.state.rate_limiter = rate_limiter
        app.state.token_validator = token_validator
        _log.info("brain_agent.started", env=settings.env)
        try:
            yield
        finally:
            _log.info("brain_agent.stopping")
            if features is not None and hasattr(features, "close"):
                await features.close()
            if graph is not None:
                await graph.close()
            if pool is not None:
                await pool.close()

    app = FastAPI(
        title="brain-agent",
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
        "brain_agent.main:app",
        host=settings.host,
        port=settings.port,
        log_config=None,
        reload=False,
    )


if __name__ == "__main__":
    run()
