"""ProfileStore: update + decay + ranking."""

from __future__ import annotations

from brain_agent_fraud.detectors import Detection
from brain_agent_fraud.profile import ProfileStore


def test_profile_store_initialises_on_first_update() -> None:
    store = ProfileStore()
    p = store.update(
        agent_id="a1",
        detections=[Detection(signal_kind="agent.split_txn", score=0.8, evidence={})],
        txn_count=10,
        now_ms=0,
    )
    assert p.composite_score >= 0.8
    assert "agent.split_txn" in p.pattern_scores


def test_profile_score_does_not_regress_within_window() -> None:
    """A subsequent quiet window should not zero out a previously hot agent."""
    store = ProfileStore(decay_per_hour=0.5)
    store.update(
        agent_id="a1",
        detections=[Detection(signal_kind="agent.split_txn", score=0.8, evidence={})],
        txn_count=10,
        now_ms=0,
    )
    store.update(
        agent_id="a1",
        detections=[],  # quiet window
        txn_count=2,
        now_ms=15 * 60 * 1000,  # 15 min later
    )
    p = store.get("a1")
    assert p is not None
    assert p.composite_score > 0.5  # decayed but not gone


def test_profile_decay_eventually_drops_score() -> None:
    store = ProfileStore(decay_per_hour=0.5)
    store.update(
        agent_id="a1",
        detections=[Detection(signal_kind="agent.split_txn", score=0.8, evidence={})],
        txn_count=10,
        now_ms=0,
    )
    store.update(
        agent_id="a1",
        detections=[],
        txn_count=0,
        now_ms=10 * 3600 * 1000,  # 10 hours later
    )
    p = store.get("a1")
    assert p is not None
    assert p.composite_score < 0.1


def test_profile_ranking_filters_by_min_score() -> None:
    store = ProfileStore()
    store.update(
        agent_id="hot",
        detections=[Detection(signal_kind="agent.split_txn", score=0.9, evidence={})],
        txn_count=10,
    )
    store.update(
        agent_id="cold",
        detections=[Detection(signal_kind="agent.split_txn", score=0.3, evidence={})],
        txn_count=2,
    )
    ranking = store.ranking(min_score=0.5)
    ids = [p.agent_id for p in ranking]
    assert "hot" in ids
    assert "cold" not in ids


def test_commission_anomalies_filter() -> None:
    store = ProfileStore()
    store.update(
        agent_id="cf",
        detections=[
            Detection(
                signal_kind="agent.commission_farming", score=0.85, evidence={}
            )
        ],
        txn_count=10,
    )
    store.update(
        agent_id="other",
        detections=[Detection(signal_kind="agent.split_txn", score=0.85, evidence={})],
        txn_count=10,
    )
    out = store.commission_anomalies()
    ids = [p.agent_id for p in out]
    assert ids == ["cf"]
