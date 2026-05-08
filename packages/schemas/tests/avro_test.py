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
    "fraud.signals.v1",
    "decisions.dispatched.v1",
    "action.tier1.v1",
    "action.tier2.v1",
    "action.tier3.v1",
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


def test_per_tier_action_topics_share_decision_dispatched_shape() -> None:
    """action.tier{1,2,3}.v1 are subjects carrying the DecisionDispatchedV1
    payload — see DECISIONS.md D-003. Verify they're identical to the
    decisions.dispatched.v1 schema."""
    base = json.loads((AVRO_DIR / "decisions.dispatched.v1.avsc").read_text())
    for tier in ("action.tier1.v1", "action.tier2.v1", "action.tier3.v1"):
        s = json.loads((AVRO_DIR / f"{tier}.avsc").read_text())
        assert s == base, f"{tier} schema must match decisions.dispatched.v1"
