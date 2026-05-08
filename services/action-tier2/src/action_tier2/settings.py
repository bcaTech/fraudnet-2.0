from __future__ import annotations

import os
from dataclasses import dataclass

SERVICE_NAME = "action-tier2"


@dataclass(frozen=True)
class Settings:
    service_name: str = SERVICE_NAME
    env: str = "dev"
    log_level: str = "INFO"

    kafka_bootstrap_servers: str = "localhost:9092"
    schema_registry_url: str = "http://localhost:8081"
    consumer_group: str = "action-tier2"

    customer_alert_url: str = ""
    do_i_know_you_url: str = ""
    momo_limit_url: str = ""
    safeguard_url: str = ""
    actuator_token: str = ""

    actuator_timeout_s: float = 2.0  # NRT — generous timeout

    # Locale fallback for subscribers without a profile-stored preference.
    default_locale: str = "en"

    # Protection-mode fallback for subscribers without a profile entry.
    # 'passive' = SMS-only, no opt-in required (DECISIONS.md D-008).
    # 'active' = SMS + USSD + app push; for portal-registered subscribers.
    default_protection_mode: str = "passive"

    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8202

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            env=os.environ.get("FRAUDNET_ENV", "dev"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            kafka_bootstrap_servers=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            schema_registry_url=os.environ.get("SCHEMA_REGISTRY_URL", "http://localhost:8081"),
            consumer_group=os.environ.get("ACTION_TIER2_GROUP", "action-tier2"),
            customer_alert_url=os.environ.get("CUSTOMER_ALERT_URL", ""),
            do_i_know_you_url=os.environ.get("DO_I_KNOW_YOU_URL", ""),
            momo_limit_url=os.environ.get("MOMO_LIMIT_URL", ""),
            safeguard_url=os.environ.get("SAFEGUARD_URL", ""),
            actuator_token=os.environ.get("ACTUATOR_TOKEN", ""),
            actuator_timeout_s=float(os.environ.get("ACTUATOR_TIMEOUT_S", "2.0")),
            default_locale=os.environ.get("FRAUDNET_DEFAULT_LOCALE", "en"),
            default_protection_mode=os.environ.get("FRAUDNET_DEFAULT_PROTECTION_MODE", "passive"),
            host=os.environ.get("ACTION_TIER2_HOST", "0.0.0.0"),  # noqa: S104
            port=int(os.environ.get("ACTION_TIER2_PORT", "8202")),
        )
