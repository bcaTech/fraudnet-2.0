from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from compliance.archive import _PARTITION_RE, _to_parquet


def test_partition_regex_matches_naming_convention() -> None:
    assert _PARTITION_RE.match("audit_events_2026_05") is not None
    assert _PARTITION_RE.match("audit_events_2026_05_extra") is None
    assert _PARTITION_RE.match("audit_events_") is None
    m = _PARTITION_RE.match("audit_events_2026_11")
    assert m is not None
    assert m.group(1) == "2026"
    assert m.group(2) == "11"


def test_to_parquet_with_empty_rows_writes_valid_file() -> None:
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    blob = _to_parquet([])
    assert isinstance(blob, bytes)
    assert len(blob) > 0
    import io
    table = pq.read_table(io.BytesIO(blob))
    assert table.num_rows == 0
    assert "id" in table.column_names
    assert "metadata_json" in table.column_names


def test_to_parquet_serialises_rows_with_metadata_json() -> None:
    pq = pytest.importorskip("pyarrow.parquet")
    rows = [
        {
            "id": uuid4(),
            "actor_id": uuid4(),
            "actor_kind": "user",
            "action": "alerts.read",
            "resource_kind": "alert",
            "resource_id": "alert-1",
            "purpose": "fraud_prevention",
            "request_id": "req-1",
            "tenant_id": "mtn-ghana",
            "metadata": {"key": "value"},
            "event_ts": datetime(2026, 5, 1, tzinfo=timezone.utc),
            "received_at": datetime(2026, 5, 1, 1, tzinfo=timezone.utc),
        }
    ]
    blob = _to_parquet(rows)
    import io
    table = pq.read_table(io.BytesIO(blob)).to_pydict()
    assert table["action"] == ["alerts.read"]
    assert table["metadata_json"] == ['{"key": "value"}']
