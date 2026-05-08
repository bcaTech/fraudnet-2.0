"""Producer / consumer config rendering."""

from __future__ import annotations

import pytest

from fraudnet.kafka.config import KafkaSettings


def test_producer_config_includes_idempotence_and_acks() -> None:
    s = KafkaSettings(
        bootstrap_servers="localhost:9092",
        schema_registry_url="http://localhost:8081",
        client_id="test",
    )
    cfg = s.producer_config()
    assert cfg["enable.idempotence"] is True
    assert cfg["acks"] == "all"
    assert cfg["compression.type"] == "zstd"


def test_consumer_config_requires_group_id() -> None:
    s = KafkaSettings(
        bootstrap_servers="localhost:9092",
        schema_registry_url="http://localhost:8081",
        client_id="test",
    )
    with pytest.raises(ValueError):
        s.consumer_config()


def test_consumer_config_disables_auto_commit() -> None:
    s = KafkaSettings(
        bootstrap_servers="localhost:9092",
        schema_registry_url="http://localhost:8081",
        client_id="test",
        group_id="test-group",
    )
    cfg = s.consumer_config()
    assert cfg["enable.auto.commit"] is False
    assert cfg["group.id"] == "test-group"


def test_sasl_only_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KAFKA_SASL_MECHANISM", raising=False)
    s = KafkaSettings.from_env(client_id="x")
    assert "sasl.mechanism" not in s.producer_config()
