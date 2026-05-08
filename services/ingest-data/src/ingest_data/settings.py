"""ingest-data runtime settings."""

from __future__ import annotations

import os
from dataclasses import dataclass

SERVICE_NAME = "ingest-data"


@dataclass(frozen=True)
class Settings:
    service_name: str = SERVICE_NAME
    env: str = "dev"
    log_level: str = "INFO"

    kafka_bootstrap_servers: str = "localhost:9092"
    schema_registry_url: str = "http://localhost:8081"

    redis_url: str = "redis://localhost:6379/3"
    # DNS resolvers and IPDR collectors retransmit aggressively on
    # backend flaps; 1h TTL gives us comfortable headroom without
    # bloating Redis at 30k events/sec sustained.
    idempotency_ttl_s: int = 3_600

    dns_webhook_shared_secret: str = ""
    ipdr_webhook_shared_secret: str = ""

    # Resolver / probe identity, embedded in events for vendor-flap debugging.
    dns_resolver_id: str = "unknown"
    ipdr_collector_id: str = "unknown"

    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8103

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            env=os.environ.get("FRAUDNET_ENV", "dev"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            kafka_bootstrap_servers=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            schema_registry_url=os.environ.get("SCHEMA_REGISTRY_URL", "http://localhost:8081"),
            redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/3"),
            idempotency_ttl_s=int(os.environ.get("DATA_IDEMPOTENCY_TTL_S", "3600")),
            dns_webhook_shared_secret=os.environ.get("DATA_DNS_WEBHOOK_SHARED_SECRET", ""),
            ipdr_webhook_shared_secret=os.environ.get("DATA_IPDR_WEBHOOK_SHARED_SECRET", ""),
            dns_resolver_id=os.environ.get("DATA_DNS_RESOLVER_ID", "unknown"),
            ipdr_collector_id=os.environ.get("DATA_IPDR_COLLECTOR_ID", "unknown"),
            host=os.environ.get("INGEST_DATA_HOST", "0.0.0.0"),  # noqa: S104
            port=int(os.environ.get("INGEST_DATA_PORT", "8103")),
        )
