"""Pure-function tests on the takedown state machine."""

from __future__ import annotations

import pytest

from api_noc.db import is_valid_transition


@pytest.mark.parametrize(
    ("source", "target", "valid"),
    [
        ("drafted", "approved", True),
        ("drafted", "filed", False),
        ("approved", "filed", True),
        ("approved", "executed", False),
        ("filed", "acknowledged", True),
        ("acknowledged", "executed", True),
        ("executed", "closed", True),
        ("closed", "drafted", False),
        ("anything", "approved", False),
        ("drafted", "closed", True),
    ],
)
def test_takedown_transitions(source: str, target: str, valid: bool) -> None:
    assert is_valid_transition(source, target) is valid
