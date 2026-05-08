"""Dependency wiring.

Lifespan-managed: the FastAPI app constructs deps once at startup and tears
them down at shutdown. Routes pull deps via `Depends(deps_dependency)`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Request

from fraudnet.kafka import AvroProducer, KafkaSettings
from fraudnet.schemas.events import MoMoEventV1
from ingest_momo.idempotency import IdempotencyCache
from ingest_momo.settings import Settings


@dataclass
class IngestDeps:
    settings: Settings
    producer: AvroProducer[MoMoEventV1]
    idempotency: IdempotencyCache


async def build_deps(
    settings: Settings,
    *,
    idempotency: IdempotencyCache | None = None,
) -> IngestDeps:
    """Construct deps. Caller is responsible for tearing them down."""
    if idempotency is None:
        from ingest_momo.idempotency import RedisIdempotencyCache

        idempotency = RedisIdempotencyCache(url=settings.redis_url)

    kafka_settings = KafkaSettings(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        schema_registry_url=settings.schema_registry_url,
        client_id=settings.service_name,
    )
    producer: AvroProducer[MoMoEventV1] = AvroProducer(
        settings=kafka_settings,
        model_cls=MoMoEventV1,
    )
    await producer.start()
    return IngestDeps(settings=settings, producer=producer, idempotency=idempotency)


async def teardown_deps(deps: IngestDeps) -> None:
    await deps.producer.stop()
    await deps.idempotency.close()


def deps_dependency(request: Request) -> IngestDeps:
    """FastAPI dependency: returns the per-app deps."""
    deps = getattr(request.app.state, "deps", None)
    if deps is None:
        raise RuntimeError("ingest_momo.deps not initialised — lifespan misconfigured")
    return deps  # type: ignore[no-any-return]


# Helper for tests that inject hand-built deps.
def install_test_deps(app: object, deps: IngestDeps) -> None:
    app.state.deps = deps  # type: ignore[attr-defined]


# Convenience type alias for endpoint signatures
DepsT = Annotated[IngestDeps, deps_dependency]
