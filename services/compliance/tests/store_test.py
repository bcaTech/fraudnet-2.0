from __future__ import annotations

from compliance.store import _audit_uuid, _ts


def test_audit_uuid_stable() -> None:
    a = _audit_uuid("aud_abc")
    b = _audit_uuid("aud_abc")
    c = _audit_uuid("aud_xyz")
    assert a == b
    assert a != c


def test_audit_uuid_well_formed() -> None:
    u = _audit_uuid("aud_test")
    # uuid5 always tags v5; sha256-truncated bytes do not.
    assert u.version == 5
    assert len(str(u)) == 36


def test_ts_round_trip() -> None:
    dt = _ts(1_700_000_000_000)
    assert dt.year == 2023
    assert dt.tzinfo is not None
