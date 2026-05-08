from __future__ import annotations

import pytest

from ingest_voice.adapter import (
    GenericProbeEvent,
    _derive_event_id,
    partition_key,
    to_canonical,
)


def _probe(**overrides: object) -> GenericProbeEvent:
    base: dict[str, object] = {
        "cdr_id": "CDR-1",
        "event_type": "CALL_START",
        "timestamp_ms": 1_700_000_000_000,
        "caller": "0241234567",
        "callee": "0207654321",
        "duration_s": 30,
        "network": "VoLTE",
    }
    base.update(overrides)
    return GenericProbeEvent.model_validate(base)


def test_call_start_round_trip() -> None:
    ev = to_canonical(_probe(), source="probe-vendor")
    assert ev.kind == "call_start"
    assert ev.caller == "+233241234567"
    assert ev.callee == "+233207654321"
    assert ev.network == "VoLTE"


def test_handover_kind_mapped() -> None:
    ev = to_canonical(_probe(event_type="HANDOFF"), source="t")
    assert ev.kind == "handover"


def test_unknown_event_type_rejected() -> None:
    with pytest.raises(ValueError):
        to_canonical(_probe(event_type="MYSTERY"), source="t")


def test_invalid_caller_rejected() -> None:
    with pytest.raises(ValueError):
        to_canonical(_probe(caller="not-a-number"), source="t")


def test_partition_key_is_caller() -> None:
    ev = to_canonical(_probe(), source="t")
    assert partition_key(ev) == "+233241234567"


def test_event_id_uses_cdr_id_when_present() -> None:
    eid = _derive_event_id(_probe(cdr_id="STABLE-CDR-42"))
    assert eid == "voice_STABLE-CDR-42"


def test_event_id_falls_back_to_natural_keys() -> None:
    a = _derive_event_id(_probe(cdr_id=None))
    b = _derive_event_id(_probe(cdr_id=None))
    assert a == b
    c = _derive_event_id(_probe(cdr_id=None, timestamp_ms=1_700_000_000_001))
    assert a != c
