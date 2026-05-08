"""FraudNet Kafka client.

Producer / consumer wrappers that hide the Avro + schema-registry plumbing
and apply FraudNet's wiring conventions: idempotent producers, dead-letter
topic per source, lag-aware health checks, structured logging.

Service code creates a producer with `AvroProducer(topic=...)` and calls
`await producer.send(payload, key=...)`. The wrapper validates against the
Pydantic class for the topic, serialises with the registry-managed Avro
schema, and writes with delivery confirmation.
"""

from fraudnet.kafka.config import KafkaSettings
from fraudnet.kafka.consumer import AvroConsumer, ConsumerHandler
from fraudnet.kafka.dlq import DLQRouter
from fraudnet.kafka.errors import (
    DeliveryError,
    KafkaConfigError,
    KafkaError,
    SchemaError,
)
from fraudnet.kafka.health import ConsumerLagProbe
from fraudnet.kafka.producer import AvroProducer

__all__ = [
    "AvroConsumer",
    "AvroProducer",
    "ConsumerHandler",
    "ConsumerLagProbe",
    "DLQRouter",
    "DeliveryError",
    "KafkaConfigError",
    "KafkaError",
    "KafkaSettings",
    "SchemaError",
]
