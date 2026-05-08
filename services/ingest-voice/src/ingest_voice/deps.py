"""ingest-voice dependency wiring."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from fraudnet.kafka import AvroProducer, KafkaSettings
from fraudnet.schemas.events import VoiceEventV1
from ingest_voice.idempotency import IdempotencyCache
from ingest_voice.settings import Settings


@dataclass
class IngestDeps:
    settings: Settings
    producer: AvroProducer[VoiceEventV1]
    idempotency: IdempotencyCache


async def build_deps(
    settings: Settings,
    *,
    idempotency: IdempotencyCache | None = None,
) -> IngestDeps:
    if idempotency is None:
        from ingest_voice.idempotency import RedisIdempotencyCache

        idempotency = RedisIdempotencyCache(url=settings.redis_url)
    kafka_settings = KafkaSettings(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        schema_registry_url=settings.schema_registry_url,
        client_id=settings.service_name,
    )
    producer: AvroProducer[VoiceEventV1] = AvroProducer(
        settings=kafka_settings,
        model_cls=VoiceEventV1,
    )
    await producer.start()
    return IngestDeps(settings=settings, producer=producer, idempotency=idempotency)


async def teardown_deps(deps: IngestDeps) -> None:
    await deps.producer.stop()
    await deps.idempotency.close()


def deps_dependency(request: Request) -> IngestDeps:
    deps = getattr(request.app.state, "deps", None)
    if deps is None:
        raise RuntimeError("ingest_voice.deps not initialised — lifespan misconfigured")
    return deps  # type: ignore[no-any-return]
