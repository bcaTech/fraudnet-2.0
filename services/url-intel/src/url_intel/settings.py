from __future__ import annotations

import os
from dataclasses import dataclass

SERVICE_NAME = "url-intel"


@dataclass(frozen=True)
class Settings:
    service_name: str = SERVICE_NAME
    env: str = "dev"
    log_level: str = "INFO"

    redis_url: str = "redis://localhost:6379/4"
    # Per-domain TTL for entries that came in via fraud.signals.v1 — protects
    # against false positives sticking around after the underlying signal
    # has decayed. Manual + feed-imported entries default to no TTL.
    signal_entry_ttl_s: int = 86400 * 30  # 30d

    # Allow-list overrides — domains in this list are never blocked, even if
    # an upstream signal says so. CSV.
    allow_list: str = (
        "google.com,facebook.com,whatsapp.com,instagram.com,youtube.com,"
        "mtn.com.gh,mtn.com,ecobank.com,gcb.com.gh,bog.gov.gh,nca.org.gh,"
        "twitter.com,x.com,microsoft.com,apple.com,amazon.com"
    )

    # Optional Kafka consumer group for the fraud.signals.v1 listener.
    kafka_bootstrap_servers: str = "localhost:9092"
    schema_registry_url: str = "http://localhost:8081"
    consumer_group: str = "url-intel"
    enable_signals_listener: bool = True

    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8312

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            env=os.environ.get("FRAUDNET_ENV", "dev"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            redis_url=os.environ.get("URL_INTEL_REDIS_URL", "redis://localhost:6379/4"),
            signal_entry_ttl_s=int(os.environ.get("URL_INTEL_SIGNAL_TTL_S", str(86400 * 30))),
            allow_list=os.environ.get(
                "URL_INTEL_ALLOW_LIST",
                "google.com,facebook.com,whatsapp.com,instagram.com,youtube.com,"
                "mtn.com.gh,mtn.com,ecobank.com,gcb.com.gh,bog.gov.gh,nca.org.gh,"
                "twitter.com,x.com,microsoft.com,apple.com,amazon.com",
            ),
            kafka_bootstrap_servers=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            schema_registry_url=os.environ.get("SCHEMA_REGISTRY_URL", "http://localhost:8081"),
            consumer_group=os.environ.get("URL_INTEL_GROUP", "url-intel"),
            enable_signals_listener=os.environ.get("URL_INTEL_LISTEN_SIGNALS", "1") == "1",
            host=os.environ.get("URL_INTEL_HOST", "0.0.0.0"),  # noqa: S104
            port=int(os.environ.get("URL_INTEL_PORT", "8312")),
        )

    def parse_allow_list(self) -> frozenset[str]:
        return frozenset(s.strip().lower() for s in self.allow_list.split(",") if s.strip())
