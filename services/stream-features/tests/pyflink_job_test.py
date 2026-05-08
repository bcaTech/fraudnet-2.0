"""Static tests on the PyFlink job's DDL builders.

We don't run pyflink in CI; we assert the SQL strings carry the right
topics, group ids, and watermark grammar so the job is wireable when
flink is available.
"""

from __future__ import annotations

from stream_features.pyflink_job import (
    FEATURES_SINK_TOPIC,
    MOMO_TOPIC,
    SMS_TOPIC,
    VOICE_TOPIC,
    _build_momo_ddl,
    _build_sink_ddl,
    _build_sms_ddl,
    _build_voice_ddl,
)


def test_voice_ddl_has_kafka_topic_and_watermark() -> None:
    ddl = _build_voice_ddl(
        bootstrap="kafka:9092", schema_registry_url="http://sr:8081", group_id="g1"
    )
    assert VOICE_TOPIC in ddl
    assert "WATERMARK FOR event_time" in ddl
    assert "kafka:9092" in ddl
    assert "avro-confluent" in ddl
    assert "group.id" in ddl and "g1" in ddl


def test_sms_ddl_carries_topic() -> None:
    ddl = _build_sms_ddl(
        bootstrap="kafka:9092", schema_registry_url="http://sr:8081", group_id="g1"
    )
    assert SMS_TOPIC in ddl


def test_momo_ddl_carries_topic_and_watermark() -> None:
    ddl = _build_momo_ddl(
        bootstrap="kafka:9092", schema_registry_url="http://sr:8081", group_id="g1"
    )
    assert MOMO_TOPIC in ddl
    assert "WATERMARK" in ddl


def test_sink_ddl_uses_upsert_kafka_with_pk() -> None:
    ddl = _build_sink_ddl(bootstrap="kafka:9092", schema_registry_url="http://sr:8081")
    assert FEATURES_SINK_TOPIC in ddl
    assert "upsert-kafka" in ddl
    assert "PRIMARY KEY" in ddl
