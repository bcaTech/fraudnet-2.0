from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, Response

from fraudnet.obs import configure_logging, configure_tracing, get_logger, metrics_endpoint
from action_tier2.actuators import (
    Actuator,
    ActuatorRegistry,
    CustomerSmsAlertActuator,
    DoIKnowYouPromptActuator,
    MoMoReviewLimitActuator,
    NoopActuator,
    SafeguardEnrollActuator,
)
from action_tier2.locale import StaticLocaleResolver, SubscriberLocaleResolver
from action_tier2.protection import (
    ProtectionModeResolver,
    StaticProtectionModeResolver,
)
from action_tier2.runner import Tier2Runner, make_settings_factory
from action_tier2.settings import Settings

_log = get_logger("action_tier2.main")


def _build_registry(
    settings: Settings,
    *,
    locale_resolver: SubscriberLocaleResolver | None = None,
) -> ActuatorRegistry:
    actuators: dict[str, Actuator] = {}
    resolver = locale_resolver or StaticLocaleResolver(default=settings.default_locale)

    def make(
        action: str,
        url: str,
        cls: type[Actuator],
        actuator_id: str,
        with_locale: bool = False,
    ) -> Actuator:
        if not url:
            return NoopActuator(action=action)
        if with_locale:
            return cls(  # type: ignore[call-arg]
                action=action,
                url=url,
                actuator_id=actuator_id,
                token=settings.actuator_token or None,
                timeout_s=settings.actuator_timeout_s,
                locale_resolver=resolver,
            )
        return cls(  # type: ignore[call-arg]
            action=action,
            url=url,
            actuator_id=actuator_id,
            token=settings.actuator_token or None,
            timeout_s=settings.actuator_timeout_s,
        )

    actuators["customer.alert_smishing"] = make(
        "customer.alert_smishing",
        settings.customer_alert_url,
        CustomerSmsAlertActuator,
        "customer-notify",
        with_locale=True,
    )
    actuators["customer.alert_spam_call"] = make(
        "customer.alert_spam_call",
        settings.customer_alert_url,
        CustomerSmsAlertActuator,
        "customer-notify",
        with_locale=True,
    )
    actuators["customer.alert_otp_fraud"] = make(
        "customer.alert_otp_fraud",
        settings.customer_alert_url,
        CustomerSmsAlertActuator,
        "customer-notify",
        with_locale=True,
    )
    actuators["customer.alert_url_blocked"] = make(
        "customer.alert_url_blocked",
        settings.customer_alert_url,
        CustomerSmsAlertActuator,
        "customer-notify",
        with_locale=True,
    )
    actuators["customer.alert_fraud"] = make(
        "customer.alert_fraud",
        settings.customer_alert_url,
        CustomerSmsAlertActuator,
        "customer-notify",
        with_locale=True,
    )
    actuators["customer.do_i_know_you_prompt"] = make(
        "customer.do_i_know_you_prompt",
        settings.do_i_know_you_url,
        DoIKnowYouPromptActuator,
        "customer-app-prompts",
        with_locale=True,
    )
    actuators["momo.review_limit"] = make(
        "momo.review_limit",
        settings.momo_limit_url,
        MoMoReviewLimitActuator,
        "momo-bss-limits",
    )
    actuators["safeguard.enroll"] = make(
        "safeguard.enroll",
        settings.safeguard_url,
        SafeguardEnrollActuator,
        "safeguard-enroll",
    )
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
        protection_resolver: ProtectionModeResolver = StaticProtectionModeResolver(
            default=settings.default_protection_mode  # type: ignore[arg-type]
        )
        runner = Tier2Runner(
            registry=registry,
            kafka_settings_factory=make_settings_factory(
                bootstrap=settings.kafka_bootstrap_servers,
                schema_registry_url=settings.schema_registry_url,
                group_id=settings.consumer_group,
            ),
            protection_resolver=protection_resolver,
        )
        app.state.registry = registry
        app.state.runner = runner
        runner_task = asyncio.create_task(runner.start(), name="tier2-runner")
        _log.info("action_tier2.started", env=settings.env)
        try:
            yield
        finally:
            _log.info("action_tier2.stopping")
            await runner.stop()
            runner_task.cancel()

    app = FastAPI(
        title="action-tier2",
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
        return {"status": "ready" if getattr(app.state, "runner", None) else "starting"}

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
        "action_tier2.main:app",
        host=settings.host,
        port=settings.port,
        log_config=None,
        reload=False,
    )


if __name__ == "__main__":
    run()
