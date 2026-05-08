"""stream-graph runtime settings."""

from __future__ import annotations

import os
from dataclasses import dataclass

SERVICE_NAME = "stream-graph"


@dataclass(frozen=True)
class Settings:
    service_name: str = SERVICE_NAME
    env: str = "dev"
    log_level: str = "INFO"

    kafka_bootstrap_servers: str = "localhost:9092"
    schema_registry_url: str = "http://localhost:8081"
    consumer_group: str = "stream-graph"

    memgraph_url: str = "bolt://localhost:7687"
    memgraph_user: str = ""
    memgraph_password: str = ""

    # Buffered writer cadence. Per §5.2 sub-minute consistency target.
    graph_buffer_max: int = 1000
    graph_flush_interval_s: float = 5.0

    # 'standalone' (in-process Memgraph batch writer) | 'cluster' (PyFlink job).
    flink_mode: str = "standalone"

    health_host: str = "0.0.0.0"  # noqa: S104
    health_port: int = 8111

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            env=os.environ.get("FRAUDNET_ENV", "dev"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            kafka_bootstrap_servers=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            schema_registry_url=os.environ.get("SCHEMA_REGISTRY_URL", "http://localhost:8081"),
            consumer_group=os.environ.get("STREAM_GRAPH_GROUP", "stream-graph"),
            memgraph_url=os.environ.get("MEMGRAPH_URL", "bolt://localhost:7687"),
            memgraph_user=os.environ.get("MEMGRAPH_USER", ""),
            memgraph_password=os.environ.get("MEMGRAPH_PASSWORD", ""),
            graph_buffer_max=int(os.environ.get("GRAPH_BUFFER_MAX", "1000")),
            graph_flush_interval_s=float(os.environ.get("GRAPH_FLUSH_INTERVAL_S", "5.0")),
            flink_mode=os.environ.get("FLINK_MODE", "standalone"),
            health_host=os.environ.get("STREAM_GRAPH_HEALTH_HOST", "0.0.0.0"),  # noqa: S104
            health_port=int(os.environ.get("STREAM_GRAPH_HEALTH_PORT", "8111")),
        )
