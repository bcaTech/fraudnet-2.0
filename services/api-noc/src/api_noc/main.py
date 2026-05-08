from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from fraudnet.auth.principal import Principal
from fraudnet.auth.token import TokenValidator, TokenValidatorConfig, extract_principal
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
from api_noc.api import router
from api_noc.db import AlertRepo, Database, RingRepo, TakedownRepo
from api_noc.settings import Settings

_log = get_logger("api_noc.main")


def create_app(
    *,
    settings: Settings | None = None,
    db: Database | None = None,
    graph: GraphClient | None = None,
    token_validator: TokenValidator | None = None,
    test_principal: Principal | None = None,
) -> FastAPI:
    """Build the FastAPI app.

    Test path: pass `db`, `graph`, and either `token_validator` or
    `test_principal` (bypass auth for unit tests).
    """
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(service=settings.service_name, level=settings.log_level)
        configure_tracing(service=settings.service_name)
        nonlocal db, graph, token_validator
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
        if token_validator is None and test_principal is None:
            token_validator = TokenValidator(
                TokenValidatorConfig(
                    issuer=settings.jwt_issuer,
                    audience=settings.jwt_audience,
                    jwks_url=settings.jwks_url,
                )
            )

        app.state.db = db
        app.state.alerts = AlertRepo(db)
        app.state.rings = RingRepo(db)
        app.state.takedowns = TakedownRepo(db)
        app.state.graph = graph
        app.state.token_validator = token_validator

        _log.info("api_noc.started", env=settings.env)
        try:
            yield
        finally:
            _log.info("api_noc.stopping")
            await db.close()
            await graph.close()

    app = FastAPI(
        title="api-noc",
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
        # Skip auth on health and metrics
        if request.url.path.startswith("/health") or request.url.path == "/metrics" or request.url.path == "/docs" or request.url.path.startswith("/openapi"):
            return await call_next(request)

        if test_principal is not None:
            request.state.principal = test_principal
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content=ErrorEnvelope(
                    error=ErrorBody(code=ErrorCode.AUTH_REQUIRED, message="missing bearer token"),
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
                    error=ErrorBody(code=ErrorCode.AUTH_INVALID_TOKEN, message="invalid token"),
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
        "api_noc.main:app",
        host=settings.host,
        port=settings.port,
        log_config=None,
        reload=False,
    )


if __name__ == "__main__":
    run()
