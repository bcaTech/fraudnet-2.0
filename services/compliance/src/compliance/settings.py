from __future__ import annotations

import os
from dataclasses import dataclass

SERVICE_NAME = "compliance"


@dataclass(frozen=True)
class Settings:
    service_name: str = SERVICE_NAME
    env: str = "dev"
    log_level: str = "INFO"

    audit_postgres_dsn: str = (
        "postgres://fraudnet:fraudnet_dev@localhost:5432/fraudnet_audit"
    )
    kafka_bootstrap_servers: str = "localhost:9092"
    schema_registry_url: str = "http://localhost:8081"
    consumer_group: str = "compliance"

    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8400

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            env=os.environ.get("FRAUDNET_ENV", "dev"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            audit_postgres_dsn=os.environ.get(
                "AUDIT_POSTGRES_DSN",
                "postgres://fraudnet:fraudnet_dev@localhost:5432/fraudnet_audit",
            ),
            kafka_bootstrap_servers=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            schema_registry_url=os.environ.get("SCHEMA_REGISTRY_URL", "http://localhost:8081"),
            consumer_group=os.environ.get("COMPLIANCE_GROUP", "compliance"),
            host=os.environ.get("COMPLIANCE_HOST", "0.0.0.0"),  # noqa: S104
            port=int(os.environ.get("COMPLIANCE_PORT", "8400")),
        )
