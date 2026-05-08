from __future__ import annotations

import os
from dataclasses import dataclass

SERVICE_NAME = "api-enterprise"


@dataclass(frozen=True)
class Settings:
    service_name: str = SERVICE_NAME
    env: str = "dev"
    log_level: str = "INFO"

    postgres_dsn: str = "postgres://fraudnet:fraudnet_dev@localhost:5432/fraudnet"
    redis_url: str = "redis://localhost:6379/5"
    memgraph_url: str = "bolt://localhost:7687"
    memgraph_user: str = ""
    memgraph_password: str = ""

    kafka_bootstrap_servers: str = "localhost:9092"
    schema_registry_url: str = "http://localhost:8081"

    # Keycloak / JWT — B2B tenants live in their own realm with `tenant_id`
    # on every issued token. The audience is fixed; new tenants share the
    # realm and are distinguished by the `tenant_id` claim, not by audience.
    jwt_issuer: str = "http://localhost:8090/realms/fraudnet-enterprise"
    jwt_audience: str = "fraudnet-enterprise"
    jwks_url: str = (
        "http://localhost:8090/realms/fraudnet-enterprise/protocol/openid-connect/certs"
    )

    # Per-tenant rate limit (token bucket). Values chosen for the typical
    # B2B workload; tunable per-tenant via `enterprise_tenants.rate_limit_*`.
    rate_limit_capacity: int = 60        # bucket size
    rate_limit_refill_per_s: float = 10  # tokens/sec

    # Federation client — queries other opcos for cross-opco ring detection.
    # Empty disables federation; `name=url` pairs comma-separated.
    federation_peers: str = ""
    federation_shared_secret: str = "dev-federation-secret-change-me"

    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8013

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            env=os.environ.get("FRAUDNET_ENV", "dev"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            postgres_dsn=os.environ.get(
                "POSTGRES_DSN",
                "postgres://fraudnet:fraudnet_dev@localhost:5432/fraudnet",
            ),
            redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/5"),
            memgraph_url=os.environ.get("MEMGRAPH_URL", "bolt://localhost:7687"),
            memgraph_user=os.environ.get("MEMGRAPH_USER", ""),
            memgraph_password=os.environ.get("MEMGRAPH_PASSWORD", ""),
            kafka_bootstrap_servers=os.environ.get(
                "KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"
            ),
            schema_registry_url=os.environ.get(
                "SCHEMA_REGISTRY_URL", "http://localhost:8081"
            ),
            jwt_issuer=os.environ.get(
                "JWT_ISSUER", "http://localhost:8090/realms/fraudnet-enterprise"
            ),
            jwt_audience=os.environ.get("JWT_AUDIENCE", "fraudnet-enterprise"),
            jwks_url=os.environ.get(
                "JWKS_URL",
                "http://localhost:8090/realms/fraudnet-enterprise/protocol/openid-connect/certs",
            ),
            rate_limit_capacity=int(os.environ.get("ENTERPRISE_RL_CAPACITY", "60")),
            rate_limit_refill_per_s=float(
                os.environ.get("ENTERPRISE_RL_REFILL_PER_S", "10")
            ),
            federation_peers=os.environ.get("FEDERATION_PEERS", ""),
            federation_shared_secret=os.environ.get(
                "FEDERATION_SHARED_SECRET",
                "dev-federation-secret-change-me",
            ),
            host=os.environ.get("API_ENTERPRISE_HOST", "0.0.0.0"),  # noqa: S104
            port=int(os.environ.get("API_ENTERPRISE_PORT", "8013")),
        )
