from __future__ import annotations

import os
from dataclasses import dataclass

SERVICE_NAME = "business-registry"


@dataclass(frozen=True)
class Settings:
    service_name: str = SERVICE_NAME
    env: str = "dev"
    log_level: str = "INFO"

    database_url: str = "postgresql://fraudnet:fraudnet_dev@localhost:5432/fraudnet"
    redis_url: str = "redis://localhost:6379/5"
    cache_ttl_s: int = 300  # 5 min — verified businesses change rarely

    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8313

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            env=os.environ.get("FRAUDNET_ENV", "dev"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            database_url=os.environ.get(
                "BUSINESS_REGISTRY_DB_URL",
                "postgresql://fraudnet:fraudnet_dev@localhost:5432/fraudnet",
            ),
            redis_url=os.environ.get("BUSINESS_REGISTRY_REDIS_URL", "redis://localhost:6379/5"),
            cache_ttl_s=int(os.environ.get("BUSINESS_REGISTRY_CACHE_TTL_S", "300")),
            host=os.environ.get("BUSINESS_REGISTRY_HOST", "0.0.0.0"),  # noqa: S104
            port=int(os.environ.get("BUSINESS_REGISTRY_PORT", "8313")),
        )
