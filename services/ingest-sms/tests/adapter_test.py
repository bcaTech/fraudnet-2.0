from __future__ import annotations

import pytest

from ingest_sms.adapter import SmscPushEvent, partition_key, to_canonical


def _push(**overrides: object) -> SmscPushEvent:
    base: dict[str, object] = {
        "smsc_msg_id": "MSG-1",
        "event_type": "MT",
        "timestamp_ms": 1_700_000_000_000,
        "sender": "0241234567",
        "recipient": "0207654321",
        "body": "You have won GHS 1000! Click https://bit.ly/scam",
    }
    base.update(overrides)
    return SmscPushEvent.model_validate(base)


class TestSmsAdapter:
    def test_no_body_capture_by_default(self) -> None:
        ev = to_canonical(_push(), source="smsc-1", smsc_id="smsc-1", allow_body_capture=False)
        assert ev.body is None
        assert ev.body_hash is not None
        assert ev.template_hash is not None
        assert ev.kind == "mt"

    def test_body_capture_when_authorised(self) -> None:
        ev = to_canonical(_push(), source="smsc-1", smsc_id="smsc-1", allow_body_capture=True)
        assert ev.body is not None and ev.body.startswith("You have won")

    def test_msisdns_normalised(self) -> None:
        ev = to_canonical(_push(), source="t", smsc_id="t", allow_body_capture=False)
        assert ev.sender == "+233241234567"
        assert ev.recipient == "+233207654321"

    def test_unknown_event_type_rejected(self) -> None:
        with pytest.raises(ValueError):
            to_canonical(
                _push(event_type="NEW"),
                source="t",
                smsc_id="t",
                allow_body_capture=False,
            )

    def test_partition_key_is_sender(self) -> None:
        ev = to_canonical(_push(), source="t", smsc_id="t", allow_body_capture=False)
        assert partition_key(ev) == "+233241234567"

    def test_smsc_id_preserved_when_supplied(self) -> None:
        ev = to_canonical(
            _push(smsc_id="smsc-2"),
            source="t",
            smsc_id="default",
            allow_body_capture=False,
        )
        assert ev.smsc_id == "smsc-2"

    def test_smsc_id_falls_back_to_settings(self) -> None:
        ev = to_canonical(
            _push(smsc_id=None),
            source="t",
            smsc_id="from-settings",
            allow_body_capture=False,
        )
        assert ev.smsc_id == "from-settings"


class TestRcsVerified:
    def test_rcs_verified_default_false(self) -> None:
        ev = to_canonical(_push(), source="t", smsc_id="t", allow_body_capture=False)
        assert ev.rcs_verified is False

    def test_rcs_verified_explicit_true(self) -> None:
        ev = to_canonical(
            _push(rcs_verified=True),
            source="t",
            smsc_id="t",
            allow_body_capture=False,
        )
        assert ev.rcs_verified is True

    def test_rcs_verified_normalises_vendor_aliases(self) -> None:
        ev = to_canonical(
            _push(verified_sender=True),
            source="t",
            smsc_id="t",
            allow_body_capture=False,
        )
        assert ev.rcs_verified is True

    def test_rcs_verified_normalises_string_truthy(self) -> None:
        ev = to_canonical(
            _push(rcs_authenticated="true"),
            source="t",
            smsc_id="t",
            allow_body_capture=False,
        )
        assert ev.rcs_verified is True
