from __future__ import annotations

import os
from dataclasses import dataclass

SERVICE_NAME = "decisions"


@dataclass(frozen=True)
class Settings:
    service_name: str = SERVICE_NAME
    env: str = "dev"
    log_level: str = "INFO"

    kafka_bootstrap_servers: str = "localhost:9092"
    schema_registry_url: str = "http://localhost:8081"
    consumer_group: str = "decisions"

    redis_url: str = "redis://localhost:6379/3"

    policy_dir: str = ""  # empty → use bundled default
    policy_hot_reload: bool = True

    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8200

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            env=os.environ.get("FRAUDNET_ENV", "dev"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            kafka_bootstrap_servers=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            schema_registry_url=os.environ.get("SCHEMA_REGISTRY_URL", "http://localhost:8081"),
            consumer_group=os.environ.get("DECISIONS_GROUP", "decisions"),
            redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/3"),
            policy_dir=os.environ.get("DECISIONS_POLICY_DIR", ""),
            policy_hot_reload=os.environ.get("DECISIONS_POLICY_HOT_RELOAD", "1") == "1",
            host=os.environ.get("DECISIONS_HOST", "0.0.0.0"),  # noqa: S104
            port=int(os.environ.get("DECISIONS_PORT", "8200")),
        )
