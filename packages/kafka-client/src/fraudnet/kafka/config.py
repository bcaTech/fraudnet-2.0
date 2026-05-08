"""Kafka client configuration.

Loaded from environment variables so each service / environment can override
without code changes. Production deployment uses Vault-injected env vars.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class KafkaSettings:
    bootstrap_servers: str
    schema_registry_url: str
    client_id: str
    security_protocol: Literal["PLAINTEXT", "SASL_SSL", "SSL"] = "PLAINTEXT"
    sasl_mechanism: str | None = None
    sasl_username: str | None = None
    sasl_password: str | None = None
    ssl_ca_location: str | None = None

    # Producer defaults — favour exactly-once on critical paths; the call site
    # may override for at-least-once topics.
    enable_idempotence: bool = True
    acks: str = "all"
    compression_type: str = "zstd"
    linger_ms: int = 5
    retries: int = 2147483647     # confluent-kafka default; bounded by delivery.timeout.ms
    delivery_timeout_ms: int = 120_000

    # Consumer defaults
    group_id: str | None = None
    auto_offset_reset: Literal["earliest", "latest"] = "earliest"
    enable_auto_commit: bool = False
    max_poll_records: int = 500
    session_timeout_ms: int = 45_000

    # Healthcheck: a consumer is unhealthy if its lag exceeds this on any
    # partition. Per-service runbook tunes these.
    lag_warn_threshold: int = 50_000
    lag_critical_threshold: int = 250_000

    extra: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, *, client_id: str, group_id: str | None = None) -> KafkaSettings:
        return cls(
            bootstrap_servers=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            schema_registry_url=os.environ.get(
                "SCHEMA_REGISTRY_URL", "http://localhost:8081"
            ),
            client_id=client_id,
            security_protocol=os.environ.get("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT"),  # type: ignore[arg-type]
            sasl_mechanism=os.environ.get("KAFKA_SASL_MECHANISM"),
            sasl_username=os.environ.get("KAFKA_SASL_USERNAME"),
            sasl_password=os.environ.get("KAFKA_SASL_PASSWORD"),
            ssl_ca_location=os.environ.get("KAFKA_SSL_CA_LOCATION"),
            group_id=group_id,
            auto_offset_reset=os.environ.get("KAFKA_AUTO_OFFSET_RESET", "earliest"),  # type: ignore[arg-type]
        )

    def producer_config(self) -> dict[str, str | int | bool]:
        cfg: dict[str, str | int | bool] = {
            "bootstrap.servers": self.bootstrap_servers,
            "client.id": self.client_id,
            "enable.idempotence": self.enable_idempotence,
            "acks": self.acks,
            "compression.type": self.compression_type,
            "linger.ms": self.linger_ms,
            "delivery.timeout.ms": self.delivery_timeout_ms,
            "security.protocol": self.security_protocol,
        }
        if self.sasl_mechanism:
            cfg["sasl.mechanism"] = self.sasl_mechanism
            cfg["sasl.username"] = self.sasl_username or ""
            cfg["sasl.password"] = self.sasl_password or ""
        if self.ssl_ca_location:
            cfg["ssl.ca.location"] = self.ssl_ca_location
        cfg.update(self.extra)
        return cfg

    def consumer_config(self) -> dict[str, str | int | bool]:
        if not self.group_id:
            raise ValueError("group_id is required for consumer config")
        cfg: dict[str, str | int | bool] = {
            "bootstrap.servers": self.bootstrap_servers,
            "client.id": self.client_id,
            "group.id": self.group_id,
            "enable.auto.commit": self.enable_auto_commit,
            "auto.offset.reset": self.auto_offset_reset,
            "session.timeout.ms": self.session_timeout_ms,
            "max.poll.interval.ms": 300_000,
            "security.protocol": self.security_protocol,
        }
        if self.sasl_mechanism:
            cfg["sasl.mechanism"] = self.sasl_mechanism
            cfg["sasl.username"] = self.sasl_username or ""
            cfg["sasl.password"] = self.sasl_password or ""
        if self.ssl_ca_location:
            cfg["ssl.ca.location"] = self.ssl_ca_location
        cfg.update(self.extra)
        return cfg
