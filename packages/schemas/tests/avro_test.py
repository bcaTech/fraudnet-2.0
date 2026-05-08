"""Tests that ship Avro schemas are valid and cover every §6.3 topic."""

from __future__ import annotations

import json
from pathlib import Path

EXPECTED_TOPICS = {
    "voice.events.v1",
    "sms.events.v1",
    "data.events.v1",
    "momo.events.v1",
    "intel.events.v1",
    "graph.mutations.v1",
    "motifs.detected.v1",
    "decisions.dispatched.v1",
    "actions.taken.v1",
    "audit.events.v1",
}


AVRO_DIR = Path(__file__).resolve().parents[1] / "avro"


def test_every_topic_has_a_schema() -> None:
    on_disk = {p.stem for p in AVRO_DIR.glob("*.avsc")}
    missing = EXPECTED_TOPICS - on_disk
    assert not missing, f"missing avro schemas: {missing}"


def test_every_schema_is_valid_json_and_record() -> None:
    for path in AVRO_DIR.glob("*.avsc"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        assert schema["type"] == "record", f"{path.name}: top-level must be record"
        assert "name" in schema
        assert "namespace" in schema
        assert schema["namespace"].startswith("gh.mtn.fraudnet"), (
            f"{path.name}: namespace must be under gh.mtn.fraudnet, got {schema['namespace']}"
        )


def test_event_envelope_fields_present_on_event_topics() -> None:
    """Every domain-event topic carries the standard envelope fields."""
    envelope = {"event_id", "event_ts_ms", "source", "tenant_id"}
    # audit.events has its own envelope (no ingest_ts_ms / source); skip it.
    skip = {"audit.events.v1"}
    for path in AVRO_DIR.glob("*.avsc"):
        if path.stem in skip:
            continue
        schema = json.loads(path.read_text(encoding="utf-8"))
        names = {f["name"] for f in schema["fields"]}
        missing = envelope - names
        assert not missing, f"{path.name}: missing envelope fields {missing}"
