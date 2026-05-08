from __future__ import annotations

import os
from dataclasses import dataclass

SERVICE_NAME = "aml-watchlist"


@dataclass(frozen=True)
class Settings:
    service_name: str = SERVICE_NAME
    env: str = "dev"
    log_level: str = "INFO"

    postgres_dsn: str = "postgres://fraudnet:fraudnet_dev@localhost:5432/fraudnet"
    redis_url: str = "redis://localhost:6379/8"

    # External feed URLs.
    un_feed_url: str = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"
    ofac_feed_url: str = "https://www.treasury.gov/ofac/downloads/sdn.csv"

    # Default match threshold; per-tenant override via the policy YAML.
    default_match_threshold: float = 0.85
    # Threshold above which a match auto-escalates to Tier 1.
    tier1_match_threshold: float = 0.90

    # Cron — feed refresh cadence in seconds. Default 24h.
    refresh_interval_s: int = 86_400

    # Auth — same realm as api-noc; SYSTEM_ADMIN can manually import.
    jwt_issuer: str = "http://localhost:8090/realms/fraudnet"
    jwt_audience: str = "fraudnet-noc"
    jwks_url: str = "http://localhost:8090/realms/fraudnet/protocol/openid-connect/certs"

    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8340

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            env=os.environ.get("FRAUDNET_ENV", "dev"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            postgres_dsn=os.environ.get(
                "POSTGRES_DSN",
                "postgres://fraudnet:fraudnet_dev@localhost:5432/fraudnet",
            ),
            redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/8"),
            un_feed_url=os.environ.get(
                "UN_FEED_URL",
                "https://scsanctions.un.org/resources/xml/en/consolidated.xml",
            ),
            ofac_feed_url=os.environ.get(
                "OFAC_FEED_URL",
                "https://www.treasury.gov/ofac/downloads/sdn.csv",
            ),
            default_match_threshold=float(
                os.environ.get("AML_THRESHOLD", "0.85")
            ),
            tier1_match_threshold=float(
                os.environ.get("AML_TIER1_THRESHOLD", "0.90")
            ),
            refresh_interval_s=int(os.environ.get("AML_REFRESH_INTERVAL_S", "86400")),
            jwt_issuer=os.environ.get(
                "JWT_ISSUER", "http://localhost:8090/realms/fraudnet"
            ),
            jwt_audience=os.environ.get("JWT_AUDIENCE", "fraudnet-noc"),
            jwks_url=os.environ.get(
                "JWKS_URL",
                "http://localhost:8090/realms/fraudnet/protocol/openid-connect/certs",
            ),
            host=os.environ.get("AML_HOST", "0.0.0.0"),  # noqa: S104
            port=int(os.environ.get("AML_PORT", "8340")),
        )
