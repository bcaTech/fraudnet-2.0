"""Pure-logic tests for the DLQ topic-mapping rule."""

from __future__ import annotations

import pytest

from fraudnet.kafka.dlq import DLQRouter


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("voice.events.v1", "voice.events.dlq.v1"),
        ("sms.events.v1", "sms.events.dlq.v1"),
        ("data.events.v1", "data.events.dlq.v1"),
        ("momo.events.v1", "momo.events.dlq.v1"),
        ("intel.events.v1", "intel.events.dlq.v1"),
        ("foo.bar.v3", "foo.bar.dlq.v3"),
    ],
)
def test_dlq_topic_naming(source: str, expected: str) -> None:
    assert DLQRouter.dlq_for(source) == expected


def test_unversioned_topic_falls_back() -> None:
    assert DLQRouter.dlq_for("legacy_topic") == "legacy_topic.dlq"
