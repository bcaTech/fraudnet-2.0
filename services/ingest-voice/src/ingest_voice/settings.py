"""ingest-voice runtime settings."""

from __future__ import annotations

import os
from dataclasses import dataclass

SERVICE_NAME = "ingest-voice"


@dataclass(frozen=True)
class Settings:
    service_name: str = SERVICE_NAME
    env: str = "dev"
    log_level: str = "INFO"

    kafka_bootstrap_servers: str = "localhost:9092"
    schema_registry_url: str = "http://localhost:8081"

    redis_url: str = "redis://localhost:6379/1"
    idempotency_ttl_s: int = 3_600  # 1h dedup window — voice CDRs redeliver fast

    webhook_shared_secret: str = ""
    vendor_id: str = "unknown"  # set per vendor integration; surfaces in event.source

    host: str = "0.0.0.0"  # noqa: S104 — bind-all is correct in container
    port: int = 8101

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            env=os.environ.get("FRAUDNET_ENV", "dev"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            kafka_bootstrap_servers=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            schema_registry_url=os.environ.get("SCHEMA_REGISTRY_URL", "http://localhost:8081"),
            redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/1"),
            idempotency_ttl_s=int(os.environ.get("VOICE_IDEMPOTENCY_TTL_S", "3600")),
            webhook_shared_secret=os.environ.get("VOICE_WEBHOOK_SHARED_SECRET", ""),
            vendor_id=os.environ.get("VOICE_VENDOR_ID", "unknown"),
            host=os.environ.get("INGEST_VOICE_HOST", "0.0.0.0"),  # noqa: S104
            port=int(os.environ.get("INGEST_VOICE_PORT", "8101")),
        )
