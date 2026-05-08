"""Contribution computation: baseline lookup, weight clamping, boost,
ranking."""

from __future__ import annotations

import pytest

from fraudnet.xai import (
    StaticBaselineProvider,
    contributions_from_evidence,
    rank_contributions,
)


def test_static_baseline_default_includes_phase_1_features() -> None:
    bp = StaticBaselineProvider.default()
    assert bp.baseline("vel_1m") == 1
    assert bp.baseline("fanout_1h") == 8
    assert bp.baseline("non_existent") is None


def test_contributions_skip_non_numeric() -> None:
    bp = StaticBaselineProvider.default()
    out = contributions_from_evidence(
        {"vel_1m": 47, "smshash_top": "abc123"},
        baselines=bp,
    )
    features = {c.feature for c in out}
    # `smshash_top` is a string — drop it from the feature view.
    assert "vel_1m" in features
    assert "smshash_top" not in features


def test_boolean_evidence_treated_as_one() -> None:
    """`rcs_verified_recent: True` becomes a 1.0 contribution."""
    bp = StaticBaselineProvider({})
    out = contributions_from_evidence(
        {"rcs_verified_recent": True},
        baselines=bp,
    )
    assert any(c.feature == "rcs_verified_recent" and c.value == 1.0 for c in out)


def test_boost_features_lift_weight() -> None:
    """A feature flagged in boost_features outranks an equally elevated
    one that wasn't keyed on."""
    bp = StaticBaselineProvider.default()
    out_no_boost = contributions_from_evidence(
        {"vel_1m": 10, "fanout_1h": 80},
        baselines=bp,
    )
    out_boost = contributions_from_evidence(
        {"vel_1m": 10, "fanout_1h": 80},
        baselines=bp,
        boost_features=("vel_1m",),
    )

    def _w(out: list, feat: str) -> float:
        return next(c.weight for c in out if c.feature == feat)

    assert _w(out_boost, "vel_1m") > _w(out_no_boost, "vel_1m")


def test_weight_is_clamped_to_unit_interval() -> None:
    """Even with a 1000× elevation, weight stays in [-1, 1]."""
    bp = StaticBaselineProvider.default()
    out = contributions_from_evidence({"vel_1m": 100_000}, baselines=bp)
    assert all(-1.0 <= c.weight <= 1.0 for c in out)


def test_rank_contributions_sorts_by_abs_weight() -> None:
    bp = StaticBaselineProvider({"a": 1, "b": 1, "c": 1})
    out = contributions_from_evidence(
        {"a": 50, "b": 5, "c": 2},
        baselines=bp,
    )
    top = rank_contributions(out, top_n=2)
    assert [c.feature for c in top] == ["a", "b"]


@pytest.mark.parametrize("feat,value", [("vel_1m", 47), ("fanout_1h", 88)])
def test_known_feature_has_baseline_attached(feat: str, value: int) -> None:
    bp = StaticBaselineProvider.default()
    [c] = contributions_from_evidence({feat: value}, baselines=bp)
    assert c.baseline is not None
    assert c.value == value
