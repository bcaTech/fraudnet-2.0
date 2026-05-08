from __future__ import annotations

import os
from dataclasses import dataclass

SERVICE_NAME = "action-tier1"


@dataclass(frozen=True)
class Settings:
    service_name: str = SERVICE_NAME
    env: str = "dev"
    log_level: str = "INFO"

    kafka_bootstrap_servers: str = "localhost:9092"
    schema_registry_url: str = "http://localhost:8081"
    consumer_group: str = "action-tier1"

    # Backend URLs. Empty → NoopActuator (logs only).
    volte_tag_url: str = ""
    url_block_url: str = ""
    sms_block_url: str = ""
    momo_friction_url: str = ""
    otp_hold_url: str = ""
    # When set, url.block routes through DnsSinkholeActuator: post the
    # domain to url-intel (allow-list aware) and then to the sinkhole URL.
    url_intel_url: str = ""
    actuator_token: str = ""

    actuator_timeout_s: float = 0.1   # 100ms cap for inline budget
    otp_hold_duration_s: int = 60     # how long the SMSC holds the OTP message

    # Defensive local allow-list at the actuator boundary. The authoritative
    # source is url-intel, but if a misconfiguration there would otherwise
    # let us sinkhole an MTN-owned or critical-services domain, this list
    # short-circuits the call with a `suppressed` outcome and logs a WARN
    # we can alert on. CSV; entries match exact and as a registrable suffix.
    sinkhole_local_allow_list: str = (
        "mtn.com.gh,mtn.com,momo.mtn.com.gh,bog.gov.gh,nca.org.gh,"
        "google.com,facebook.com,whatsapp.com,apple.com,microsoft.com,amazon.com"
    )

    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8201

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            env=os.environ.get("FRAUDNET_ENV", "dev"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            kafka_bootstrap_servers=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            schema_registry_url=os.environ.get("SCHEMA_REGISTRY_URL", "http://localhost:8081"),
            consumer_group=os.environ.get("ACTION_TIER1_GROUP", "action-tier1"),
            volte_tag_url=os.environ.get("VOLTE_TAG_URL", ""),
            url_block_url=os.environ.get("URL_BLOCK_URL", ""),
            sms_block_url=os.environ.get("SMS_BLOCK_URL", ""),
            momo_friction_url=os.environ.get("MOMO_FRICTION_URL", ""),
            otp_hold_url=os.environ.get("OTP_HOLD_URL", ""),
            url_intel_url=os.environ.get("URL_INTEL_URL", ""),
            actuator_token=os.environ.get("ACTUATOR_TOKEN", ""),
            actuator_timeout_s=float(os.environ.get("ACTUATOR_TIMEOUT_S", "0.1")),
            otp_hold_duration_s=int(os.environ.get("OTP_HOLD_DURATION_S", "60")),
            sinkhole_local_allow_list=os.environ.get(
                "SINKHOLE_LOCAL_ALLOW_LIST",
                "mtn.com.gh,mtn.com,momo.mtn.com.gh,bog.gov.gh,nca.org.gh,"
                "google.com,facebook.com,whatsapp.com,apple.com,microsoft.com,amazon.com",
            ),
            host=os.environ.get("ACTION_TIER1_HOST", "0.0.0.0"),  # noqa: S104
            port=int(os.environ.get("ACTION_TIER1_PORT", "8201")),
        )

    def parse_sinkhole_allow_list(self) -> frozenset[str]:
        return frozenset(
            s.strip().lower() for s in self.sinkhole_local_allow_list.split(",") if s.strip()
        )
