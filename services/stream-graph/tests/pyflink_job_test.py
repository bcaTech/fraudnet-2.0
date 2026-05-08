from __future__ import annotations

from stream_graph.pyflink_job import (
    MUTATIONS_TOPIC,
    VOICE_TOPIC,
    _momo_source_ddl,
    _mutations_sink_ddl,
    _sms_source_ddl,
    _voice_source_ddl,
)


def test_voice_ddl_carries_topic_and_watermark() -> None:
    ddl = _voice_source_ddl(bootstrap="kafka:9092", schema_registry_url="http://sr", group_id="g1")
    assert VOICE_TOPIC in ddl
    assert "WATERMARK" in ddl


def test_sink_ddl_writes_to_mutations_topic() -> None:
    ddl = _mutations_sink_ddl(bootstrap="kafka:9092", schema_registry_url="http://sr")
    assert MUTATIONS_TOPIC in ddl
    assert "edge_kind" in ddl


def test_sms_and_momo_ddl_have_event_time() -> None:
    sms = _sms_source_ddl(bootstrap="kafka:9092", schema_registry_url="http://sr", group_id="g")
    momo = _momo_source_ddl(bootstrap="kafka:9092", schema_registry_url="http://sr", group_id="g")
    assert "event_time" in sms and "WATERMARK" in sms
    assert "event_time" in momo and "WATERMARK" in momo
