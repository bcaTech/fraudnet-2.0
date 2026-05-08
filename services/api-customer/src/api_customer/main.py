from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

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
from api_customer.api import router
from api_customer.otp import HttpOtpAdapter, InMemoryOtpAdapter, OtpAdapter
from api_customer.session import SessionTokenIssuer
from api_customer.settings import Settings

_log = get_logger("api_customer.main")


def _build_otp(settings: Settings) -> OtpAdapter:
    if settings.otp_service_url:
        return HttpOtpAdapter(
            url=settings.otp_service_url,
            token=settings.otp_service_token,
            timeout_s=2.0,
        )
    return InMemoryOtpAdapter()


def create_app(
    *,
    settings: Settings | None = None,
    otp: OtpAdapter | None = None,
    pool: asyncpg.Pool | None = None,
    intel_producer: object | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(service=settings.service_name, level=settings.log_level)
        configure_tracing(service=settings.service_name)

        nonlocal otp, pool, intel_producer
        if otp is None:
            otp = _build_otp(settings)
        if pool is None:
            pool = await asyncpg.create_pool(settings.postgres_dsn, min_size=2, max_size=10)
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

        app.state.otp = otp
        app.state.pool = pool
        app.state.intel_producer = intel_producer
        app.state.session = SessionTokenIssuer(
            secret=settings.session_secret,
            ttl_s=settings.session_ttl_s,
        )
        _log.info("api_customer.started", env=settings.env)
        try:
            yield
        finally:
            _log.info("api_customer.stopping")
            await otp.close()
            if pool is not None:
                await pool.close()
            if intel_producer is not None and hasattr(intel_producer, "stop"):
                await intel_producer.stop()  # type: ignore[func-returns-value]

    app = FastAPI(
        title="api-customer",
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
        "api_customer.main:app",
        host=settings.host,
        port=settings.port,
        log_config=None,
        reload=False,
    )


if __name__ == "__main__":
    run()
