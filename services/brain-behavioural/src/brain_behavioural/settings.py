from __future__ import annotations

import os
from dataclasses import dataclass

SERVICE_NAME = "brain-behavioural"


@dataclass(frozen=True)
class Settings:
    service_name: str = SERVICE_NAME
    env: str = "dev"
    log_level: str = "INFO"

    kafka_bootstrap_servers: str = "localhost:9092"
    schema_registry_url: str = "http://localhost:8081"
    consumer_group: str = "brain-behavioural"

    aerospike_hosts: str = "localhost:3010"

    use_model_registry: bool = True
    model_registry_endpoint: str = "http://localhost:9000"
    model_registry_bucket: str = "fraudnet-models"
    model_registry_access_key: str = "fraudnet"
    model_registry_secret_key: str = "fraudnet_dev_minio"

    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8300

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            env=os.environ.get("FRAUDNET_ENV", "dev"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            kafka_bootstrap_servers=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            schema_registry_url=os.environ.get("SCHEMA_REGISTRY_URL", "http://localhost:8081"),
            consumer_group=os.environ.get("BRAIN_BEHAVIOURAL_GROUP", "brain-behavioural"),
            aerospike_hosts=os.environ.get("AEROSPIKE_HOSTS", "localhost:3010"),
            use_model_registry=os.environ.get("BRAIN_BEHAVIOURAL_USE_REGISTRY", "1") == "1",
            model_registry_endpoint=os.environ.get(
                "MODEL_REGISTRY_ENDPOINT", "http://localhost:9000"
            ),
            model_registry_bucket=os.environ.get("MODEL_REGISTRY_BUCKET", "fraudnet-models"),
            model_registry_access_key=os.environ.get("MODEL_REGISTRY_ACCESS_KEY", "fraudnet"),
            model_registry_secret_key=os.environ.get(
                "MODEL_REGISTRY_SECRET_KEY", "fraudnet_dev_minio"
            ),
            host=os.environ.get("BRAIN_BEHAVIOURAL_HOST", "0.0.0.0"),  # noqa: S104
            port=int(os.environ.get("BRAIN_BEHAVIOURAL_PORT", "8300")),
        )
