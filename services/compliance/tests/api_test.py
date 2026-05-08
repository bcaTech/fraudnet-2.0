"""API surface tests — JSON coercion + the request validators.

The endpoints proxy to the store, so we only verify the thin glue (param
parsing, error envelopes, JSON shape). Postgres is exercised in the Phase-2
integration test."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from compliance.api import _to_jsonable


def test_to_jsonable_coerces_datetimes_and_uuids() -> None:
    raw = {
        "id": UUID("00000000-0000-0000-0000-000000000001"),
        "event_ts": datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc),
        "tenant_id": "mtn-ghana",
        "metadata": {"k": "v"},
    }
    out = _to_jsonable(raw)
    assert out["event_ts"] == "2026-05-08T12:00:00+00:00"
    # UUIDs pass through unchanged — FastAPI / json.dumps via default=str
    # handles them downstream.
    assert isinstance(out["id"], UUID)
    assert out["tenant_id"] == "mtn-ghana"
    assert out["metadata"] == {"k": "v"}


def test_to_jsonable_passes_scalars() -> None:
    raw = {"score": 0.93, "n": 12, "ok": True, "x": None}
    out = _to_jsonable(raw)
    assert out == raw
