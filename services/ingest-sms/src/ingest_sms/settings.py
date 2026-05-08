"""ingest-sms runtime settings."""

from __future__ import annotations

import os
from dataclasses import dataclass

SERVICE_NAME = "ingest-sms"


@dataclass(frozen=True)
class Settings:
    service_name: str = SERVICE_NAME
    env: str = "dev"
    log_level: str = "INFO"

    kafka_bootstrap_servers: str = "localhost:9092"
    schema_registry_url: str = "http://localhost:8081"

    redis_url: str = "redis://localhost:6379/2"
    idempotency_ttl_s: int = 7_200  # 2h: SMSC redelivery is bursty

    webhook_shared_secret: str = ""
    smsc_id: str = "unknown"

    # Body content authorisation. CLAUDE.md §5.1 — body access is gated on a
    # regulatory purpose. Production deployment binds this from Vault and the
    # service emits an audit event for every body-bearing event.
    allow_body_capture: bool = False

    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8102

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            env=os.environ.get("FRAUDNET_ENV", "dev"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            kafka_bootstrap_servers=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            schema_registry_url=os.environ.get("SCHEMA_REGISTRY_URL", "http://localhost:8081"),
            redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/2"),
            idempotency_ttl_s=int(os.environ.get("SMS_IDEMPOTENCY_TTL_S", "7200")),
            webhook_shared_secret=os.environ.get("SMS_WEBHOOK_SHARED_SECRET", ""),
            smsc_id=os.environ.get("SMS_SMSC_ID", "unknown"),
            allow_body_capture=os.environ.get("SMS_ALLOW_BODY_CAPTURE", "false").lower()
            in {"1", "true", "yes"},
            host=os.environ.get("INGEST_SMS_HOST", "0.0.0.0"),  # noqa: S104
            port=int(os.environ.get("INGEST_SMS_PORT", "8102")),
        )
