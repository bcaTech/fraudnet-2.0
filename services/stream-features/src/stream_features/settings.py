"""stream-features runtime settings."""

from __future__ import annotations

import os
from dataclasses import dataclass

SERVICE_NAME = "stream-features"


@dataclass(frozen=True)
class Settings:
    service_name: str = SERVICE_NAME
    env: str = "dev"
    log_level: str = "INFO"

    kafka_bootstrap_servers: str = "localhost:9092"
    schema_registry_url: str = "http://localhost:8081"
    consumer_group: str = "stream-features"

    aerospike_hosts: str = "localhost:3010"     # comma-separated host:port
    feature_ttl_s: int = 86_400                  # 24h, matches longest window

    # Window cadence — flush per-key state every N events or every M seconds,
    # whichever comes first. Kept small in dev for fast iteration.
    flush_every_events: int = 100
    flush_every_seconds: float = 5.0

    # 'standalone' (Phase-1 in-process runner) | 'cluster' (PyFlink job).
    flink_mode: str = "standalone"

    # Healthcheck server
    health_host: str = "0.0.0.0"  # noqa: S104
    health_port: int = 8110

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            env=os.environ.get("FRAUDNET_ENV", "dev"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            kafka_bootstrap_servers=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            schema_registry_url=os.environ.get("SCHEMA_REGISTRY_URL", "http://localhost:8081"),
            consumer_group=os.environ.get("STREAM_FEATURES_GROUP", "stream-features"),
            aerospike_hosts=os.environ.get("AEROSPIKE_HOSTS", "localhost:3010"),
            feature_ttl_s=int(os.environ.get("FEATURE_TTL_S", "86400")),
            flush_every_events=int(os.environ.get("FLUSH_EVERY_EVENTS", "100")),
            flush_every_seconds=float(os.environ.get("FLUSH_EVERY_SECONDS", "5.0")),
            flink_mode=os.environ.get("FLINK_MODE", "standalone"),
            health_host=os.environ.get("STREAM_FEATURES_HEALTH_HOST", "0.0.0.0"),  # noqa: S104
            health_port=int(os.environ.get("STREAM_FEATURES_HEALTH_PORT", "8110")),
        )
