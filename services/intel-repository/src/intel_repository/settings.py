from __future__ import annotations

import os
from dataclasses import dataclass

SERVICE_NAME = "intel-repository"


@dataclass(frozen=True)
class Settings:
    service_name: str = SERVICE_NAME
    env: str = "dev"
    log_level: str = "INFO"

    postgres_dsn: str = "postgres://fraudnet:fraudnet_dev@localhost:5432/fraudnet"
    redis_url: str = "redis://localhost:6379/9"

    kafka_bootstrap_servers: str = "localhost:9092"
    schema_registry_url: str = "http://localhost:8081"

    # TTL per kind (seconds). 90d default; tighter for fast-moving kinds
    # (template hashes go stale faster than known-mule MSISDNs).
    ttl_default_s: int = 90 * 24 * 3600
    ttl_scam_template_s: int = 30 * 24 * 3600
    ttl_spoof_indicator_s: int = 7 * 24 * 3600

    # Redis cache TTL for hot lookups.
    cache_ttl_s: int = 300

    # JWT (api-noc realm)
    jwt_issuer: str = "http://localhost:8090/realms/fraudnet"
    jwt_audience: str = "fraudnet-noc"
    jwks_url: str = "http://localhost:8090/realms/fraudnet/protocol/openid-connect/certs"

    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8360

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            env=os.environ.get("FRAUDNET_ENV", "dev"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            postgres_dsn=os.environ.get(
                "POSTGRES_DSN",
                "postgres://fraudnet:fraudnet_dev@localhost:5432/fraudnet",
            ),
            redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/9"),
            kafka_bootstrap_servers=os.environ.get(
                "KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"
            ),
            schema_registry_url=os.environ.get(
                "SCHEMA_REGISTRY_URL", "http://localhost:8081"
            ),
            ttl_default_s=int(
                os.environ.get("INTEL_TTL_DEFAULT_S", str(90 * 24 * 3600))
            ),
            ttl_scam_template_s=int(
                os.environ.get("INTEL_TTL_SCAM_TEMPLATE_S", str(30 * 24 * 3600))
            ),
            ttl_spoof_indicator_s=int(
                os.environ.get("INTEL_TTL_SPOOF_INDICATOR_S", str(7 * 24 * 3600))
            ),
            cache_ttl_s=int(os.environ.get("INTEL_CACHE_TTL_S", "300")),
            jwt_issuer=os.environ.get(
                "JWT_ISSUER", "http://localhost:8090/realms/fraudnet"
            ),
            jwt_audience=os.environ.get("JWT_AUDIENCE", "fraudnet-noc"),
            jwks_url=os.environ.get(
                "JWKS_URL",
                "http://localhost:8090/realms/fraudnet/protocol/openid-connect/certs",
            ),
            host=os.environ.get("INTEL_HOST", "0.0.0.0"),  # noqa: S104
            port=int(os.environ.get("INTEL_PORT", "8360")),
        )
