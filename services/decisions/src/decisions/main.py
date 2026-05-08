from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, Response

from fraudnet.kafka import AvroProducer, KafkaSettings
from fraudnet.obs import configure_logging, configure_tracing, get_logger, metrics_endpoint
from fraudnet.schemas.events import DecisionDispatchedV1
from fraudnet.schemas.types import LatencyTier
from decisions.dispatcher import DecisionDispatcher
from decisions.hot_reload import PolicyHotReloader
from decisions.policy import Policy, discover_default_policy, load_all
from decisions.runner import DecisionRunner, make_settings_factory
from decisions.settings import Settings
from decisions.suppression import RedisSuppressionStore

_log = get_logger("decisions.main")


def _load_policy(settings: Settings) -> Policy:
    if settings.policy_dir:
        return load_all(Path(settings.policy_dir))
    return discover_default_policy()


def create_app(*, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(service=settings.service_name, level=settings.log_level)
        configure_tracing(service=settings.service_name)

        policy = _load_policy(settings)
        _log.info(
            "decisions.policy_loaded",
            policy_id=policy.id,
            version=policy.version,
            rule_count=len(policy.rules),
            fingerprint=policy.fingerprint(),
        )

        kafka_settings = KafkaSettings(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            schema_registry_url=settings.schema_registry_url,
            client_id=settings.service_name,
        )
        audit_producer: AvroProducer[DecisionDispatchedV1] = AvroProducer(
            settings=kafka_settings,
            model_cls=DecisionDispatchedV1,
        )
        await audit_producer.start()

        tier_producers: dict[LatencyTier, AvroProducer[DecisionDispatchedV1]] = {}
        for tier, topic in (
            (LatencyTier.TIER1_INLINE, "action.tier1.v1"),
            (LatencyTier.TIER2_NRT, "action.tier2.v1"),
            (LatencyTier.TIER3_INVESTIGATION, "action.tier3.v1"),
        ):
            p: AvroProducer[DecisionDispatchedV1] = AvroProducer(
                settings=kafka_settings,
                model_cls=DecisionDispatchedV1,
                topic=topic,
            )
            await p.start()
            tier_producers[tier] = p

        dispatcher = DecisionDispatcher(
            audit_producer=audit_producer,
            tier_producers=tier_producers,
            policy=policy,
        )
        suppression = RedisSuppressionStore(url=settings.redis_url)
        runner = DecisionRunner(
            policy=policy,
            suppression=suppression,
            dispatcher=dispatcher,
            kafka_settings_factory=make_settings_factory(
                bootstrap=settings.kafka_bootstrap_servers,
                schema_registry_url=settings.schema_registry_url,
                group_id=settings.consumer_group,
            ),
        )

        app.state.policy = policy
        app.state.runner = runner

        # Hot-reload bound to the same directory the policy was loaded from.
        from pathlib import Path
        policy_dir = Path(settings.policy_dir) if settings.policy_dir else (
            Path(__file__).resolve().parent.parent / "policies"
        )
        reloader = PolicyHotReloader(
            directory=policy_dir,
            dispatcher=dispatcher,
            runner=runner,
        )
        reloader.record_initial(policy)

        def _on_policy_change(new_policy: Policy) -> None:
            app.state.policy = new_policy

        reloader.on_change(_on_policy_change)
        if settings.policy_hot_reload:
            reloader.start()
        app.state.reloader = reloader

        runner_task = asyncio.create_task(runner.start(), name="decisions-runner")
        _log.info("decisions.started", env=settings.env)
        try:
            yield
        finally:
            _log.info("decisions.stopping")
            reloader.stop()
            await runner.stop()
            await audit_producer.stop()
            for p in tier_producers.values():
                await p.stop()
            runner_task.cancel()

    app = FastAPI(
        title="decisions",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )

    @app.get("/health/live", include_in_schema=False)
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", include_in_schema=False)
    async def readiness() -> dict[str, object]:
        runner = getattr(app.state, "runner", None)
        if runner is None:
            return {"status": "starting"}
        return {"status": "ready", "service": settings.service_name}

    @app.get("/policy", include_in_schema=False)
    async def policy_summary() -> dict[str, object]:
        policy: Policy | None = getattr(app.state, "policy", None)
        if policy is None:
            return {"loaded": False}
        return {
            "loaded": True,
            "id": policy.id,
            "version": policy.version,
            "fingerprint": policy.fingerprint(),
            "rule_count": len(policy.rules),
            "rules": [
                {
                    "id": r.id,
                    "action": r.action,
                    "tier": r.tier.value,
                    "suppression_window_s": r.suppression_window_s,
                }
                for r in policy.rules
            ],
        }

    @app.get("/policy/history")
    async def policy_history() -> dict[str, object]:
        reloader: PolicyHotReloader | None = getattr(app.state, "reloader", None)
        if reloader is None:
            return {"history": []}
        return {
            "history": [
                {
                    "id": v.id,
                    "version": v.version,
                    "fingerprint": v.fingerprint,
                    "rule_count": v.rule_count,
                    "loaded_at_ms": v.loaded_at_ms,
                    "source_files": list(v.source_files),
                }
                for v in reversed(reloader.history)
            ]
        }

    @app.post("/policy/reload")
    async def policy_reload() -> dict[str, object]:
        """Trigger an immediate reload from disk. Useful when the watcher
        is disabled or for forcing a reload after a config-map update in K8s."""
        reloader: PolicyHotReloader | None = getattr(app.state, "reloader", None)
        if reloader is None:
            return {"status": "no_reloader"}
        version = reloader.reload_now()
        if version is None:
            return {"status": "noop_or_invalid"}
        return {
            "status": "applied",
            "id": version.id,
            "version": version.version,
            "fingerprint": version.fingerprint,
            "rule_count": version.rule_count,
        }

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        body, content_type = metrics_endpoint()()
        return PlainTextResponse(body, media_type=content_type)

    return app


def __getattr__(name: str) -> object:
    if name == "app":
        return create_app()
    raise AttributeError(name)


def run() -> None:
    import uvicorn

    settings = Settings.from_env()
    uvicorn.run(
        "decisions.main:app",
        host=settings.host,
        port=settings.port,
        log_config=None,
        reload=False,
    )


if __name__ == "__main__":
    run()
