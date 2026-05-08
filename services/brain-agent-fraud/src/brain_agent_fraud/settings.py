from __future__ import annotations

import os
from dataclasses import dataclass

SERVICE_NAME = "brain-agent-fraud"


@dataclass(frozen=True)
class Settings:
    service_name: str = SERVICE_NAME
    env: str = "dev"
    log_level: str = "INFO"

    postgres_dsn: str = "postgres://fraudnet:fraudnet_dev@localhost:5432/fraudnet"
    memgraph_url: str = "bolt://localhost:7687"
    memgraph_user: str = ""
    memgraph_password: str = ""
    kafka_bootstrap_servers: str = "localhost:9092"
    schema_registry_url: str = "http://localhost:8081"

    # Detector thresholds (CLAUDE.md §5.4 says decision policy is YAML
    # but detector thresholds are tuning, not policy — they live in
    # settings).
    commission_farming_min_pairs: int = 5     # min same-customer cash-in/cash-out cycles per hour
    commission_farming_window_s: int = 3600   # 1h

    split_txn_threshold_minor: int = 1_000_000   # GHS 10,000.00 in pesewas
    split_txn_max_size_minor: int = 200_000      # GHS 2,000.00 — splits stay under this
    split_txn_min_pieces: int = 3
    split_txn_window_s: int = 1800               # 30 min

    phantom_dormancy_window_s: int = 30 * 24 * 3600  # 30d
    phantom_min_dormancy_score: int = 0              # ≤ 0 prior txns = "dormant"

    collusion_min_shared_agents: int = 2  # via device sharing
    collusion_window_s: int = 24 * 3600

    float_excess_threshold_minor: int = 50_000_000  # GHS 500k = excessive float
    float_movement_pairs_min: int = 4

    # JWT — same realm as api-noc.
    jwt_issuer: str = "http://localhost:8090/realms/fraudnet"
    jwt_audience: str = "fraudnet-noc"
    jwks_url: str = "http://localhost:8090/realms/fraudnet/protocol/openid-connect/certs"

    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8350

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
            kafka_bootstrap_servers=os.environ.get(
                "KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"
            ),
            schema_registry_url=os.environ.get(
                "SCHEMA_REGISTRY_URL", "http://localhost:8081"
            ),
            jwt_issuer=os.environ.get(
                "JWT_ISSUER", "http://localhost:8090/realms/fraudnet"
            ),
            jwt_audience=os.environ.get("JWT_AUDIENCE", "fraudnet-noc"),
            jwks_url=os.environ.get(
                "JWKS_URL",
                "http://localhost:8090/realms/fraudnet/protocol/openid-connect/certs",
            ),
            host=os.environ.get("BRAIN_AGENT_FRAUD_HOST", "0.0.0.0"),  # noqa: S104
            port=int(os.environ.get("BRAIN_AGENT_FRAUD_PORT", "8350")),
        )
