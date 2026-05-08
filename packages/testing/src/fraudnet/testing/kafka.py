"""Ephemeral Kafka via Testcontainers for integration tests.

Use:

    @pytest.fixture
    async def kafka() -> AsyncIterator[EphemeralKafka]:
        async with EphemeralKafka() as k:
            yield k
"""

from __future__ import annotations

import contextlib
from typing import Any

try:
    from testcontainers.kafka import KafkaContainer
except ImportError:  # pragma: no cover — testcontainers extra
    KafkaContainer = None  # type: ignore[assignment, misc]


class EphemeralKafka(contextlib.AbstractAsyncContextManager["EphemeralKafka"]):
    def __init__(self) -> None:
        if KafkaContainer is None:
            raise RuntimeError(
                "testcontainers[kafka] not installed; pip install testcontainers[kafka]"
            )
        self._container: Any = KafkaContainer("confluentinc/cp-kafka:7.7.1")
        self._bootstrap: str | None = None

    async def __aenter__(self) -> "EphemeralKafka":
        self._container.start()
        self._bootstrap = self._container.get_bootstrap_server()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self._container.stop()

    @property
    def bootstrap_servers(self) -> str:
        if self._bootstrap is None:
            raise RuntimeError("kafka not started")
        return self._bootstrap
