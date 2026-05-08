from __future__ import annotations

import os
from dataclasses import dataclass

SERVICE_NAME = "brain-content"


@dataclass(frozen=True)
class Settings:
    service_name: str = SERVICE_NAME
    env: str = "dev"
    log_level: str = "INFO"

    kafka_bootstrap_servers: str = "localhost:9092"
    schema_registry_url: str = "http://localhost:8081"
    consumer_group: str = "brain-content"

    # Comma-separated dev defaults; production loads from a ConfigMap.
    bad_domains: str = "bit.ly/scam,scam-momo.com,winaprize.example"
    bad_url_template_hashes: str = ""
    bad_body_hashes: str = ""

    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8301

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            env=os.environ.get("FRAUDNET_ENV", "dev"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            kafka_bootstrap_servers=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            schema_registry_url=os.environ.get("SCHEMA_REGISTRY_URL", "http://localhost:8081"),
            consumer_group=os.environ.get("BRAIN_CONTENT_GROUP", "brain-content"),
            bad_domains=os.environ.get(
                "BRAIN_CONTENT_BAD_DOMAINS",
                "bit.ly/scam,scam-momo.com,winaprize.example",
            ),
            bad_url_template_hashes=os.environ.get("BRAIN_CONTENT_BAD_TEMPLATE_HASHES", ""),
            bad_body_hashes=os.environ.get("BRAIN_CONTENT_BAD_BODY_HASHES", ""),
            host=os.environ.get("BRAIN_CONTENT_HOST", "0.0.0.0"),  # noqa: S104
            port=int(os.environ.get("BRAIN_CONTENT_PORT", "8301")),
        )

    def parse_list(self, attr: str) -> list[str]:
        v = getattr(self, attr) or ""
        return [s.strip() for s in v.split(",") if s.strip()]
