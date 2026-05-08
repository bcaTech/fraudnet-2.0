from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, Response

from fraudnet.kafka import KafkaSettings
from fraudnet.obs import configure_logging, configure_tracing, get_logger, metrics_endpoint
from action_tier1.actuators import (
    Actuator,
    ActuatorRegistry,
    MoMoSendWithCareActuator,
    NoopActuator,
    OtpHoldActuator,
    SmsBlockActuator,
    UrlBlockActuator,
    VolteTagActuator,
)
from action_tier1.runner import Tier1Runner, make_settings_factory
from action_tier1.settings import Settings

_log = get_logger("action_tier1.main")


def _build_registry(settings: Settings) -> ActuatorRegistry:
    actuators: dict[str, Actuator] = {}

    def make(action: str, url: str, cls: type[Actuator], actuator_id: str) -> Actuator:
        if not url:
            return NoopActuator(action=action)
        return cls(  # type: ignore[call-arg]
            action=action,
            url=url,
            actuator_id=actuator_id,
            token=settings.actuator_token or None,
            timeout_s=settings.actuator_timeout_s,
        )

    actuators["volte.tag_suspected_spam"] = make(
        "volte.tag_suspected_spam", settings.volte_tag_url, VolteTagActuator, "ims-core"
    )
    actuators["url.block"] = make(
        "url.block", settings.url_block_url, UrlBlockActuator, "dns-sinkhole"
    )
    actuators["sms.block"] = make(
        "sms.block", settings.sms_block_url, SmsBlockActuator, "smsc-block"
    )
    actuators["momo.send_with_care"] = make(
        "momo.send_with_care",
        settings.momo_friction_url,
        MoMoSendWithCareActuator,
        "momo-bss",
    )
    if settings.otp_hold_url:
        actuators["otp.hold_and_alert"] = OtpHoldActuator(
            action="otp.hold_and_alert",
            url=settings.otp_hold_url,
            actuator_id="smsc-otp-hold",
            timeout_s=settings.actuator_timeout_s,
            token=settings.actuator_token or None,
            hold_duration_s=settings.otp_hold_duration_s,
        )
    else:
        actuators["otp.hold_and_alert"] = NoopActuator(action="otp.hold_and_alert")
    return ActuatorRegistry(actuators)


def create_app(
    *,
    settings: Settings | None = None,
    registry: ActuatorRegistry | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    registry = registry or _build_registry(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(service=settings.service_name, level=settings.log_level)
        configure_tracing(service=settings.service_name)

        kafka_settings = KafkaSettings(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            schema_registry_url=settings.schema_registry_url,
            client_id=settings.service_name,
        )
        runner = Tier1Runner(
            registry=registry,
            kafka_settings=kafka_settings,
            kafka_settings_factory=make_settings_factory(
                bootstrap=settings.kafka_bootstrap_servers,
                schema_registry_url=settings.schema_registry_url,
                group_id=settings.consumer_group,
            ),
        )
        app.state.registry = registry
        app.state.runner = runner
        runner_task = asyncio.create_task(runner.start(), name="tier1-runner")
        _log.info("action_tier1.started", env=settings.env)
        try:
            yield
        finally:
            _log.info("action_tier1.stopping")
            await runner.stop()
            runner_task.cancel()

    app = FastAPI(
        title="action-tier1",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )

    @app.get("/health/live", include_in_schema=False)
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", include_in_schema=False)
    async def readiness() -> dict[str, str]:
        runner = getattr(app.state, "runner", None)
        return {"status": "ready" if runner else "starting"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        body, content_type = metrics_endpoint()()
        return PlainTextResponse(body, media_type=content_type)

    @app.get("/registry", include_in_schema=False)
    async def registry_summary() -> dict[str, list[str]]:
        return {"actions": list(registry._actuators.keys())}  # type: ignore[union-attr]

    return app


def __getattr__(name: str) -> object:
    if name == "app":
        return create_app()
    raise AttributeError(name)


def run() -> None:
    import uvicorn

    settings = Settings.from_env()
    uvicorn.run(
        "action_tier1.main:app",
        host=settings.host,
        port=settings.port,
        log_config=None,
        reload=False,
    )


if __name__ == "__main__":
    run()
