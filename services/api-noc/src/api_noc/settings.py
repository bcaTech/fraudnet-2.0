from __future__ import annotations

import os
from dataclasses import dataclass

SERVICE_NAME = "api-noc"


@dataclass(frozen=True)
class Settings:
    service_name: str = SERVICE_NAME
    env: str = "dev"
    log_level: str = "INFO"

    postgres_dsn: str = "postgres://fraudnet:fraudnet_dev@localhost:5432/fraudnet"
    memgraph_url: str = "bolt://localhost:7687"
    memgraph_user: str = ""
    memgraph_password: str = ""

    # Keycloak / JWT
    jwt_issuer: str = "http://localhost:8090/realms/fraudnet"
    jwt_audience: str = "fraudnet-noc"
    jwks_url: str = "http://localhost:8090/realms/fraudnet/protocol/openid-connect/certs"

    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8010

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            env=os.environ.get("FRAUDNET_ENV", "dev"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            postgres_dsn=os.environ.get(
                "POSTGRES_DSN",
                "postgres://fraudnet:fraudnet_dev@localhost:5432/fraudnet",
            ),
            memgraph_url=os.environ.get("MEMGRAPH_URL", "bolt://localhost:7687"),
            memgraph_user=os.environ.get("MEMGRAPH_USER", ""),
            memgraph_password=os.environ.get("MEMGRAPH_PASSWORD", ""),
            jwt_issuer=os.environ.get(
                "JWT_ISSUER", "http://localhost:8090/realms/fraudnet"
            ),
            jwt_audience=os.environ.get("JWT_AUDIENCE", "fraudnet-noc"),
            jwks_url=os.environ.get(
                "JWKS_URL",
                "http://localhost:8090/realms/fraudnet/protocol/openid-connect/certs",
            ),
            host=os.environ.get("API_NOC_HOST", "0.0.0.0"),  # noqa: S104
            port=int(os.environ.get("API_NOC_PORT", "8010")),
        )
