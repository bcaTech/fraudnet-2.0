"""ingest-momo runtime settings.

Loaded from environment variables. Production deployment injects Vault-backed
secrets via env. Defaults match the docker-compose dev plane.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

SERVICE_NAME = "ingest-momo"


@dataclass(frozen=True)
class Settings:
    service_name: str = SERVICE_NAME
    env: str = "dev"
    log_level: str = "INFO"

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"
    schema_registry_url: str = "http://localhost:8081"

    # Redis — used for idempotency dedup.
    redis_url: str = "redis://localhost:6379/0"
    idempotency_ttl_s: int = 86_400  # 24h dedup window — bigger than typical reversal window

    # Webhook auth
    webhook_shared_secret: str = ""  # set via env in non-dev environments

    # Server
    host: str = "0.0.0.0"  # noqa: S104 — bind-all is correct inside the container
    port: int = 8100

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            env=os.environ.get("FRAUDNET_ENV", "dev"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            kafka_bootstrap_servers=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            schema_registry_url=os.environ.get("SCHEMA_REGISTRY_URL", "http://localhost:8081"),
            redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            idempotency_ttl_s=int(os.environ.get("MOMO_IDEMPOTENCY_TTL_S", "86400")),
            webhook_shared_secret=os.environ.get("MOMO_WEBHOOK_SHARED_SECRET", ""),
            host=os.environ.get("INGEST_MOMO_HOST", "0.0.0.0"),  # noqa: S104
            port=int(os.environ.get("INGEST_MOMO_PORT", "8100")),
        )
