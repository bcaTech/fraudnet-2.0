"""Detector tests — five patterns + composite + ranking."""

from __future__ import annotations

from brain_agent_fraud.detectors import (
    CollusionCohort,
    composite_agent_score,
    detect_collusion,
    detect_commission_farming,
    detect_float_manipulation,
    detect_phantom_customer,
    detect_split_txn,
    pattern_count_by_kind,
)
from brain_agent_fraud.state import AgentTxn


def _txn(
    *,
    kind: str,
    cp: str,
    amount: int = 10_000,
    ts_ms: int = 0,
    cp_kind: str = "wallet",
    channel: str | None = "agent",
) -> AgentTxn:
    return AgentTxn(
        txn_id=f"t_{ts_ms}",
        kind=kind,
        counterparty_kind=cp_kind,
        counterparty_id=cp,
        amount_minor=amount,
        ts_ms=ts_ms,
        channel=channel,
    )


# ---------------------------------------------------------------------------
# Commission farming
# ---------------------------------------------------------------------------


def test_commission_farming_fires_on_5_cycles_same_customer() -> None:
    txns = []
    base = 1_000_000
    for i in range(5):
        txns.append(_txn(kind="cash_in", cp="cust1", ts_ms=base + i * 60_000))
        txns.append(_txn(kind="cash_out", cp="cust1", ts_ms=base + i * 60_000 + 30_000))
    out = detect_commission_farming(txns, min_pairs=5)
    assert out is not None
    assert out.signal_kind == "agent.commission_farming"
    assert out.evidence["cycles"] == 5


def test_commission_farming_below_threshold_returns_none() -> None:
    txns = [
        _txn(kind="cash_in", cp="cust1", ts_ms=1000),
        _txn(kind="cash_out", cp="cust1", ts_ms=2000),
    ]
    assert detect_commission_farming(txns, min_pairs=5) is None


def test_commission_farming_ignores_unpaired_events() -> None:
    """5 cash-ins without paired cash-outs is high-volume but not
    commission farming."""
    txns = [_txn(kind="cash_in", cp="cust1", ts_ms=i * 1000) for i in range(5)]
    assert detect_commission_farming(txns, min_pairs=5) is None


# ---------------------------------------------------------------------------
# Split txn
# ---------------------------------------------------------------------------


def test_split_txn_fires_on_structuring() -> None:
    """5 × GHS 1,500 to the same counterparty = GHS 7,500 total above
    threshold GHS 7,000, all pieces below GHS 2,000."""
    txns = [
        _txn(kind="cash_out", cp="recv1", amount=150_000, ts_ms=i * 60_000)
        for i in range(5)
    ]
    out = detect_split_txn(
        txns,
        threshold_minor=700_000,
        max_piece_minor=200_000,
        min_pieces=3,
    )
    assert out is not None
    assert out.evidence["pieces"] == 5


def test_split_txn_skips_when_total_below_threshold() -> None:
    txns = [
        _txn(kind="cash_out", cp="recv1", amount=10_000, ts_ms=i * 60_000)
        for i in range(5)
    ]
    out = detect_split_txn(
        txns,
        threshold_minor=1_000_000,
        max_piece_minor=200_000,
        min_pieces=3,
    )
    assert out is None


def test_split_txn_ignores_oversized_pieces() -> None:
    """A single GHS 5,000 + 4 small pieces should NOT count the 5k as
    a split (it's the kind of legit transaction the user would actually
    monitor)."""
    txns = [
        _txn(kind="cash_out", cp="r1", amount=500_000, ts_ms=0),  # too big
        _txn(kind="cash_out", cp="r1", amount=50_000, ts_ms=1000),
        _txn(kind="cash_out", cp="r1", amount=50_000, ts_ms=2000),
    ]
    out = detect_split_txn(
        txns,
        threshold_minor=900_000,
        max_piece_minor=200_000,
        min_pieces=3,
    )
    assert out is None


# ---------------------------------------------------------------------------
# Phantom customer
# ---------------------------------------------------------------------------


class _History:
    def __init__(self, prior: dict[str, int]) -> None:
        self._prior = prior

    def prior_txn_count(self, counterparty_id: str) -> int:
        return self._prior.get(counterparty_id, 0)


def test_phantom_customer_fires_on_three_zero_history_counterparties() -> None:
    txns = [
        _txn(kind="cash_in", cp="new1", ts_ms=1),
        _txn(kind="cash_in", cp="new2", ts_ms=2),
        _txn(kind="cash_in", cp="new3", ts_ms=3),
    ]
    out = detect_phantom_customer(
        txns, history=_History({}), min_phantom_count=3
    )
    assert out is not None
    assert out.evidence["phantom_counterparties"] == 3


def test_phantom_customer_skips_when_counterparties_have_history() -> None:
    txns = [
        _txn(kind="cash_in", cp="known1", ts_ms=1),
        _txn(kind="cash_in", cp="known2", ts_ms=2),
        _txn(kind="cash_in", cp="known3", ts_ms=3),
    ]
    out = detect_phantom_customer(
        txns,
        history=_History({"known1": 5, "known2": 8, "known3": 12}),
        min_phantom_count=3,
    )
    assert out is None


# ---------------------------------------------------------------------------
# Collusion
# ---------------------------------------------------------------------------


class _Cohorts:
    def __init__(self, cohort: CollusionCohort | None) -> None:
        self._cohort = cohort

    def cohort_for(self, agent_id: str) -> CollusionCohort | None:
        return self._cohort


def test_collusion_fires_when_cohort_is_present() -> None:
    cohort = CollusionCohort(
        cohort_id="c1",
        agents=("a1", "a2", "a3"),
        shared_devices=("d1",),
        sequential_count=4,
    )
    out = detect_collusion("a1", [], cohorts=_Cohorts(cohort), min_cohort_size=2)
    assert out is not None
    assert out.evidence["cohort_size"] == 3
    assert out.evidence["shared_device_count"] == 1


def test_collusion_skips_when_no_cohort() -> None:
    assert detect_collusion("a1", [], cohorts=_Cohorts(None)) is None


# ---------------------------------------------------------------------------
# Float manipulation
# ---------------------------------------------------------------------------


def test_float_manipulation_fires_on_excess_balance() -> None:
    txns = [
        _txn(kind="cash_in", cp=f"c{i}", amount=10_000_000, ts_ms=i * 1000)
        for i in range(7)
    ]
    out = detect_float_manipulation(
        txns,
        excess_threshold_minor=50_000_000,
        movement_pairs_min=4,
    )
    assert out is not None
    assert out.evidence["excess"] is True


def test_float_manipulation_fires_on_internal_movement() -> None:
    txns = [
        _txn(
            kind="p2p_transfer",
            cp=f"agent{i}",
            cp_kind="agent",
            amount=100_000,
            ts_ms=i * 1000,
        )
        for i in range(5)
    ]
    out = detect_float_manipulation(
        txns,
        excess_threshold_minor=10_000_000_000,  # impossible threshold
        movement_pairs_min=4,
    )
    assert out is not None
    assert out.evidence["movement"] is True


def test_float_manipulation_skips_normal_activity() -> None:
    txns = [
        _txn(kind="cash_in", cp="c1", amount=50_000, ts_ms=0),
        _txn(kind="cash_out", cp="c1", amount=50_000, ts_ms=1000),
    ]
    assert detect_float_manipulation(txns) is None


# ---------------------------------------------------------------------------
# Composite
# ---------------------------------------------------------------------------


def test_composite_score_soft_or() -> None:
    """Two .9 scores compose to ~.99 (1 - 0.1 * 0.1), not 1.8."""
    from brain_agent_fraud.detectors import Detection

    score = composite_agent_score(
        [
            Detection(signal_kind="agent.commission_farming", score=0.9, evidence={}),
            Detection(signal_kind="agent.split_txn", score=0.9, evidence={}),
        ]
    )
    assert 0.98 <= score <= 1.0


def test_pattern_count_by_kind() -> None:
    from brain_agent_fraud.detectors import Detection

    counts = pattern_count_by_kind(
        [
            Detection(signal_kind="agent.commission_farming", score=0.9, evidence={}),
            Detection(signal_kind="agent.commission_farming", score=0.7, evidence={}),
            Detection(signal_kind="agent.split_txn", score=0.8, evidence={}),
        ]
    )
    assert counts == {"agent.commission_farming": 2, "agent.split_txn": 1}
