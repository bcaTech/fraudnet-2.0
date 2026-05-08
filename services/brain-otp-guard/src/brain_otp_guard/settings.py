from __future__ import annotations

import os
from dataclasses import dataclass

SERVICE_NAME = "brain-otp-guard"


@dataclass(frozen=True)
class Settings:
    service_name: str = SERVICE_NAME
    env: str = "dev"
    log_level: str = "INFO"

    kafka_bootstrap_servers: str = "localhost:9092"
    schema_registry_url: str = "http://localhost:8081"
    consumer_group: str = "brain-otp-guard"

    redis_url: str = "redis://localhost:6379/3"
    # Time an inbound call stays "active" in the registry after CALL_START
    # without an explicit CALL_END. Calls usually close cleanly; this TTL
    # protects against missed CALL_END events.
    active_call_ttl_s: int = 900  # 15 min

    # Suppression window per recipient — don't alert the same MSISDN about
    # OTP-during-call more than once in this window.
    suppression_window_s: int = 300  # 5 min

    # Comma-separated list of bank / fintech short codes whose SMS we should
    # treat as OTP-bearing. Production loads from a ConfigMap.
    bank_short_codes: str = "MTN,ECOBANK,GCB,STANBIC,FBN,UBA,CALBANK,ZENITH,FIDELITY,ABSA,GTBANK"

    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8311

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            env=os.environ.get("FRAUDNET_ENV", "dev"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            kafka_bootstrap_servers=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            schema_registry_url=os.environ.get("SCHEMA_REGISTRY_URL", "http://localhost:8081"),
            consumer_group=os.environ.get("BRAIN_OTP_GROUP", "brain-otp-guard"),
            redis_url=os.environ.get("BRAIN_OTP_REDIS_URL", "redis://localhost:6379/3"),
            active_call_ttl_s=int(os.environ.get("BRAIN_OTP_ACTIVE_CALL_TTL_S", "900")),
            suppression_window_s=int(os.environ.get("BRAIN_OTP_SUPPRESSION_S", "300")),
            bank_short_codes=os.environ.get(
                "BRAIN_OTP_BANK_SHORT_CODES",
                "MTN,ECOBANK,GCB,STANBIC,FBN,UBA,CALBANK,ZENITH,FIDELITY,ABSA,GTBANK",
            ),
            host=os.environ.get("BRAIN_OTP_HOST", "0.0.0.0"),  # noqa: S104
            port=int(os.environ.get("BRAIN_OTP_PORT", "8311")),
        )

    def parse_bank_short_codes(self) -> frozenset[str]:
        return frozenset(s.strip().upper() for s in self.bank_short_codes.split(",") if s.strip())
