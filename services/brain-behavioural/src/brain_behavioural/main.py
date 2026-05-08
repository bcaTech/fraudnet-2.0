from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from fraudnet.features import AerospikeFeatureStore, FeatureStore
from fraudnet.kafka import AvroProducer, KafkaSettings
from fraudnet.obs import configure_logging, configure_tracing, get_logger
from fraudnet.schemas.signals import SignalEventV1
from brain_behavioural.api import router
from brain_behavioural.lgbm_scorer import LightGBMScorer
from brain_behavioural.runner import BehaviouralRunner, make_settings_factory
from business_registry.client import (
    BusinessRegistryClient,
    HttpBusinessRegistryClient,
    NoopBusinessRegistryClient,
)
from brain_behavioural.scorer import HeuristicScorer, Scorer
from brain_behavioural.settings import Settings

_log = get_logger("brain_behavioural.main")


def _build_scorer(settings: Settings) -> Scorer:
    """Try the registry first; fall back to the heuristic if no champion or
    the registry is unreachable. Either path is operationally valid — dev
    environments without MinIO still work."""
    if not settings.use_model_registry:
        return HeuristicScorer()
    try:
        from fraudnet.registry import ModelRegistry, RegistryError

        registry = ModelRegistry(
            endpoint_url=settings.model_registry_endpoint,
            bucket=settings.model_registry_bucket,
            access_key=settings.model_registry_access_key,
            secret_key=settings.model_registry_secret_key,
        )
        scorer = LightGBMScorer.load_from_registry(registry)
        # If neither model loaded, just use the heuristic to avoid log spam.
        if scorer._number is None and scorer._wallet is None:  # noqa: SLF001
            _log.info("brain_behavioural.no_models_in_registry")
            return HeuristicScorer()
        return scorer
    except (ImportError, Exception) as exc:  # noqa: BLE001
        _log.warning("brain_behavioural.registry_unavailable", error=str(exc))
        return HeuristicScorer()


def _aerospike_hosts(spec: str) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for piece in spec.split(","):
        piece = piece.strip()
        if not piece:
            continue
        host, _, port = piece.partition(":")
        out.append((host, int(port or "3000")))
    return out


def create_app(
    *,
    settings: Settings | None = None,
    scorer: Scorer | None = None,
    feature_store: FeatureStore | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()

    if scorer is not None and feature_store is not None:
        # Test path — install deps directly.
        app = FastAPI(
            title="brain-behavioural",
            version="0.1.0",
            docs_url="/docs",
            redoc_url=None,
        )
        app.state.scorer = scorer
        app.state.feature_store = feature_store
        app.include_router(router)
        return app

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(service=settings.service_name, level=settings.log_level)
        configure_tracing(service=settings.service_name)
        store = AerospikeFeatureStore(hosts=_aerospike_hosts(settings.aerospike_hosts))
        scorer_inst: Scorer = _build_scorer(settings)
        kafka_settings = KafkaSettings(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            schema_registry_url=settings.schema_registry_url,
            client_id=settings.service_name,
        )
        producer: AvroProducer[SignalEventV1] = AvroProducer(
            settings=kafka_settings,
            model_cls=SignalEventV1,
        )
        await producer.start()

        registry_client: BusinessRegistryClient
        if settings.business_registry_url:
            registry_client = HttpBusinessRegistryClient(
                base_url=settings.business_registry_url
            )
        else:
            registry_client = NoopBusinessRegistryClient()

        runner = BehaviouralRunner(
            scorer=scorer_inst,
            feature_store=store,
            signal_producer=producer,
            kafka_settings_factory=make_settings_factory(
                bootstrap=settings.kafka_bootstrap_servers,
                schema_registry_url=settings.schema_registry_url,
                group_id=settings.consumer_group,
            ),
            business_registry=registry_client,
        )
        app.state.scorer = scorer_inst
        app.state.feature_store = store
        app.state.runner = runner
        runner_task = asyncio.create_task(runner.start(), name="behavioural-runner")
        _log.info("brain_behavioural.started", env=settings.env)
        try:
            yield
        finally:
            _log.info("brain_behavioural.stopping")
            await runner.stop()
            runner_task.cancel()

    app = FastAPI(
        title="brain-behavioural",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )
    app.include_router(router)
    return app


def __getattr__(name: str) -> object:
    if name == "app":
        return create_app()
    raise AttributeError(name)


def run() -> None:
    import uvicorn

    settings = Settings.from_env()
    uvicorn.run(
        "brain_behavioural.main:app",
        host=settings.host,
        port=settings.port,
        log_config=None,
        reload=False,
    )


if __name__ == "__main__":
    run()
