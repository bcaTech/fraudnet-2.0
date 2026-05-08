from __future__ import annotations

import os
from dataclasses import dataclass

SERVICE_NAME = "api-customer"


@dataclass(frozen=True)
class Settings:
    service_name: str = SERVICE_NAME
    env: str = "dev"
    log_level: str = "INFO"

    postgres_dsn: str = "postgres://fraudnet:fraudnet_dev@localhost:5432/fraudnet"
    redis_url: str = "redis://localhost:6379/4"

    kafka_bootstrap_servers: str = "localhost:9092"
    schema_registry_url: str = "http://localhost:8081"

    # Session JWT signing — HS256 for Phase 1; RS256 once the security
    # team's KMS provisioning lands.
    session_secret: str = "dev-customer-session-secret-change-me"
    session_ttl_s: int = 1800  # 30 min

    # OTP service backend. Empty → in-memory dev OTP (returns deterministic
    # code 123456 for any MSISDN, see otp.py).
    otp_service_url: str = ""
    otp_service_token: str = ""
    otp_ttl_s: int = 300

    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8011

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            env=os.environ.get("FRAUDNET_ENV", "dev"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            postgres_dsn=os.environ.get(
                "POSTGRES_DSN",
                "postgres://fraudnet:fraudnet_dev@localhost:5432/fraudnet",
            ),
            redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/4"),
            kafka_bootstrap_servers=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            schema_registry_url=os.environ.get("SCHEMA_REGISTRY_URL", "http://localhost:8081"),
            session_secret=os.environ.get(
                "CUSTOMER_SESSION_SECRET", "dev-customer-session-secret-change-me"
            ),
            session_ttl_s=int(os.environ.get("CUSTOMER_SESSION_TTL_S", "1800")),
            otp_service_url=os.environ.get("OTP_SERVICE_URL", ""),
            otp_service_token=os.environ.get("OTP_SERVICE_TOKEN", ""),
            otp_ttl_s=int(os.environ.get("OTP_TTL_S", "300")),
            host=os.environ.get("API_CUSTOMER_HOST", "0.0.0.0"),  # noqa: S104
            port=int(os.environ.get("API_CUSTOMER_PORT", "8011")),
        )
