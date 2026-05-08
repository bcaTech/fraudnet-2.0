from __future__ import annotations

import os
from dataclasses import dataclass

SERVICE_NAME = "brain-agent"


@dataclass(frozen=True)
class Settings:
    service_name: str = SERVICE_NAME
    env: str = "dev"
    log_level: str = "INFO"

    postgres_dsn: str = "postgres://fraudnet:fraudnet_dev@localhost:5432/fraudnet"
    redis_url: str = "redis://localhost:6379/7"
    memgraph_url: str = "bolt://localhost:7687"
    memgraph_user: str = ""
    memgraph_password: str = ""
    aerospike_hosts: str = "localhost:3000"

    # Anthropic API. ANTHROPIC_API_KEY required in prod (Vault); dev uses
    # the in-memory stub LLM unless a real key is present.
    anthropic_api_key: str = ""
    # Pinned to the latest Opus per CLAUDE.md cutoff.
    anthropic_model: str = "claude-opus-4-7"
    anthropic_max_tokens: int = 4096
    anthropic_timeout_s: float = 60.0

    # JWT (api-noc realm — analysts have FRAUD_ANALYST/FRAUD_LEAD).
    jwt_issuer: str = "http://localhost:8090/realms/fraudnet"
    jwt_audience: str = "fraudnet-noc"
    jwks_url: str = "http://localhost:8090/realms/fraudnet/protocol/openid-connect/certs"

    # Per-analyst rate limit. Default 10 per hour; tuned for LLM cost.
    rate_limit_capacity: int = 10
    rate_limit_refill_per_s: float = 10 / 3600.0  # full refill in 1h

    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8330

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            env=os.environ.get("FRAUDNET_ENV", "dev"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            postgres_dsn=os.environ.get(
                "POSTGRES_DSN",
                "postgres://fraudnet:fraudnet_dev@localhost:5432/fraudnet",
            ),
            redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/7"),
            memgraph_url=os.environ.get("MEMGRAPH_URL", "bolt://localhost:7687"),
            memgraph_user=os.environ.get("MEMGRAPH_USER", ""),
            memgraph_password=os.environ.get("MEMGRAPH_PASSWORD", ""),
            aerospike_hosts=os.environ.get("AEROSPIKE_HOSTS", "localhost:3000"),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            anthropic_model=os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-7"),
            anthropic_max_tokens=int(os.environ.get("ANTHROPIC_MAX_TOKENS", "4096")),
            anthropic_timeout_s=float(os.environ.get("ANTHROPIC_TIMEOUT_S", "60")),
            jwt_issuer=os.environ.get(
                "JWT_ISSUER", "http://localhost:8090/realms/fraudnet"
            ),
            jwt_audience=os.environ.get("JWT_AUDIENCE", "fraudnet-noc"),
            jwks_url=os.environ.get(
                "JWKS_URL",
                "http://localhost:8090/realms/fraudnet/protocol/openid-connect/certs",
            ),
            rate_limit_capacity=int(os.environ.get("BRAIN_AGENT_RL_CAPACITY", "10")),
            rate_limit_refill_per_s=float(
                os.environ.get(
                    "BRAIN_AGENT_RL_REFILL_PER_S", str(10 / 3600.0)
                )
            ),
            host=os.environ.get("BRAIN_AGENT_HOST", "0.0.0.0"),  # noqa: S104
            port=int(os.environ.get("BRAIN_AGENT_PORT", "8330")),
        )
