"""Kafka-client typed exceptions."""

from __future__ import annotations

from fraudnet.schemas.errors import FraudNetError


class KafkaError(FraudNetError):
    """Base for all Kafka-client errors."""


class KafkaConfigError(KafkaError):
    """Configuration is wrong — bootstrap servers, SASL, schema registry, etc."""


class SchemaError(KafkaError):
    """Avro schema is missing or incompatible with the registry."""


class DeliveryError(KafkaError):
    """Producer failed to deliver a message after retries exhausted."""

    def __init__(self, message: str, *, topic: str, key: str | None = None) -> None:
        super().__init__(message, details={"topic": topic, "key": key})
