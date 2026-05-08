from __future__ import annotations

import os
from dataclasses import dataclass

SERVICE_NAME = "brain-graph"


@dataclass(frozen=True)
class Settings:
    service_name: str = SERVICE_NAME
    env: str = "dev"
    log_level: str = "INFO"

    kafka_bootstrap_servers: str = "localhost:9092"
    schema_registry_url: str = "http://localhost:8081"

    memgraph_url: str = "bolt://localhost:7687"
    memgraph_user: str = ""
    memgraph_password: str = ""

    # Subgraph extraction caps. Larger windows yield fatter components and
    # better community signal but cost CPU; tuned for the dev stack.
    extract_window_hours: int = 24
    extract_max_nodes: int = 5_000

    # Cadence for the scheduled batch (seconds). 15 min per CLAUDE.md §5.3.
    batch_interval_s: int = 900

    # Phase 4 cross-opco federation. `name=url` pairs comma-separated.
    # Empty disables federation; the analyser then runs single-opco only.
    federation_peers: str = ""
    federation_shared_secret: str = "dev-federation-secret-change-me"

    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8302

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            env=os.environ.get("FRAUDNET_ENV", "dev"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            kafka_bootstrap_servers=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            schema_registry_url=os.environ.get("SCHEMA_REGISTRY_URL", "http://localhost:8081"),
            memgraph_url=os.environ.get("MEMGRAPH_URL", "bolt://localhost:7687"),
            memgraph_user=os.environ.get("MEMGRAPH_USER", ""),
            memgraph_password=os.environ.get("MEMGRAPH_PASSWORD", ""),
            extract_window_hours=int(os.environ.get("BRAIN_GRAPH_WINDOW_HOURS", "24")),
            extract_max_nodes=int(os.environ.get("BRAIN_GRAPH_MAX_NODES", "5000")),
            batch_interval_s=int(os.environ.get("BRAIN_GRAPH_BATCH_INTERVAL_S", "900")),
            federation_peers=os.environ.get("FEDERATION_PEERS", ""),
            federation_shared_secret=os.environ.get(
                "FEDERATION_SHARED_SECRET",
                "dev-federation-secret-change-me",
            ),
            host=os.environ.get("BRAIN_GRAPH_HOST", "0.0.0.0"),  # noqa: S104
            port=int(os.environ.get("BRAIN_GRAPH_PORT", "8302")),
        )
