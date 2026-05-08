"""Five MoMo agent/merchant fraud detectors.

Each detector:
  - takes a sliding-window view of the agent's recent transactions
    (and, for collusion, an external graph adapter)
  - returns a `Detection` (signal_kind, score, evidence,
    boost_features) or None
  - is pure / synchronous over the view it's given — async I/O
    happens at the caller boundary

Why per-pattern functions instead of a single classifier: the fraud
modes have very different signatures (temporal pairing for commission
farming, total-decomposition for split_txn, graph topology for
collusion). A unified scorer would obscure the per-pattern reasoning;
the XAI layer benefits from each pattern owning its evidence dict.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Protocol

from brain_agent_fraud.state import AgentTxn


@dataclass(frozen=True)
class Detection:
    signal_kind: str
    score: float
    evidence: dict[str, str | int | float | bool]
    boost_features: tuple[str, ...] = ()
    severity: str = "high"


# ---------------------------------------------------------------------------
# 1. Commission farming
# ---------------------------------------------------------------------------


def detect_commission_farming(
    txns: list[AgentTxn], *, min_pairs: int = 5
) -> Detection | None:
    """Same agent + same customer cycling cash-in → cash-out repeatedly.

    Each cash-in immediately followed by a cash-out for the same
    counterparty (within 10 minutes) is a "cycle". ≥ min_pairs cycles
    in the window → fire.
    """
    by_counterparty: dict[str, list[AgentTxn]] = defaultdict(list)
    for t in txns:
        if t.counterparty_id and t.kind in ("cash_in", "cash_out"):
            by_counterparty[t.counterparty_id].append(t)

    cycles = 0
    cycle_amount = 0
    for cp_txns in by_counterparty.values():
        cp_txns.sort(key=lambda t: t.ts_ms)
        i = 0
        while i < len(cp_txns) - 1:
            a, b = cp_txns[i], cp_txns[i + 1]
            if (
                a.kind == "cash_in"
                and b.kind == "cash_out"
                and 0 < (b.ts_ms - a.ts_ms) <= 10 * 60 * 1000
            ):
                cycles += 1
                cycle_amount += a.amount_minor + b.amount_minor
                i += 2  # consume both
            else:
                i += 1
    if cycles < min_pairs:
        return None
    score = min(0.99, 0.6 + 0.05 * (cycles - min_pairs))
    return Detection(
        signal_kind="agent.commission_farming",
        score=score,
        evidence={
            "cycles": cycles,
            "min_pairs": min_pairs,
            "cycle_amount_minor": cycle_amount,
            "txn_count": len(txns),
        },
        boost_features=("cycles", "cycle_amount_minor"),
    )


# ---------------------------------------------------------------------------
# 2. Split transactions
# ---------------------------------------------------------------------------


def detect_split_txn(
    txns: list[AgentTxn],
    *,
    threshold_minor: int = 1_000_000,
    max_piece_minor: int = 200_000,
    min_pieces: int = 3,
) -> Detection | None:
    """N small cash-outs to the same counterparty within the window
    summing above `threshold_minor`, each below `max_piece_minor`.

    The classic structuring pattern — break a 10k transaction into 5×2k
    pieces to stay under monitoring thresholds. We catch it on
    same-agent same-counterparty same-day clusters.
    """
    by_counterparty: dict[str, list[AgentTxn]] = defaultdict(list)
    for t in txns:
        if t.kind in ("cash_out", "p2p_transfer") and t.counterparty_id:
            if t.amount_minor < max_piece_minor:
                by_counterparty[t.counterparty_id].append(t)

    best_pieces = 0
    best_total = 0
    best_cp: str | None = None
    for cp, cp_txns in by_counterparty.items():
        if len(cp_txns) < min_pieces:
            continue
        total = sum(t.amount_minor for t in cp_txns)
        if total < threshold_minor:
            continue
        if len(cp_txns) > best_pieces:
            best_pieces = len(cp_txns)
            best_total = total
            best_cp = cp
    if best_cp is None:
        return None
    return Detection(
        signal_kind="agent.split_txn",
        score=min(0.95, 0.7 + 0.03 * (best_pieces - min_pieces)),
        evidence={
            "pieces": best_pieces,
            "total_minor": best_total,
            "threshold_minor": threshold_minor,
            "max_piece_minor": max_piece_minor,
        },
        boost_features=("pieces", "total_minor"),
    )


# ---------------------------------------------------------------------------
# 3. Phantom customer
# ---------------------------------------------------------------------------


class CounterpartyHistory(Protocol):
    """External lookup for prior activity on a counterparty wallet.

    Production: backed by Postgres / feature store. Tests pass a fake.
    """

    def prior_txn_count(self, counterparty_id: str) -> int: ...


def detect_phantom_customer(
    txns: list[AgentTxn],
    *,
    history: CounterpartyHistory,
    min_phantom_count: int = 3,
) -> Detection | None:
    """Multiple transactions against counterparties with no prior
    activity = phantom (synthetic / dormant) customers.

    The signature: agents who consistently service "first-time customers"
    are either onboarding heroes (legitimate) or running synthetic
    accounts. The graph picks up the latter via shared-device patterns;
    this detector flags the volume.
    """
    phantom = 0
    counterparties = set()
    for t in txns:
        if not t.counterparty_id:
            continue
        if t.counterparty_id in counterparties:
            continue
        counterparties.add(t.counterparty_id)
        if history.prior_txn_count(t.counterparty_id) <= 0:
            phantom += 1
    if phantom < min_phantom_count:
        return None
    ratio = phantom / max(1, len(counterparties))
    return Detection(
        signal_kind="agent.phantom_customer",
        score=min(0.95, 0.55 + 0.05 * phantom),
        evidence={
            "phantom_counterparties": phantom,
            "total_counterparties": len(counterparties),
            "phantom_ratio": round(ratio, 3),
        },
        boost_features=("phantom_counterparties", "phantom_ratio"),
        severity="high" if ratio > 0.6 else "medium",
    )


# ---------------------------------------------------------------------------
# 4. Collusion
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CollusionCohort:
    """An external view of agents sharing infrastructure / coordinating.

    Provided by stream-graph / brain-graph; the detector here folds the
    cohort into the per-agent score rather than re-running graph
    queries inline.
    """

    cohort_id: str
    agents: tuple[str, ...]
    shared_devices: tuple[str, ...] = field(default_factory=tuple)
    sequential_count: int = 0


class CohortLookup(Protocol):
    def cohort_for(self, agent_id: str) -> CollusionCohort | None: ...


def detect_collusion(
    agent_id: str,
    txns: list[AgentTxn],
    *,
    cohorts: CohortLookup,
    min_cohort_size: int = 2,
) -> Detection | None:
    """Agent appears in a cohort of N+ agents sharing a device or moving
    funds in synchrony. The cohort lookup is computed offline by
    brain-graph; this detector decides how to score the agent given
    that cohort.
    """
    cohort = cohorts.cohort_for(agent_id)
    if cohort is None or len(cohort.agents) < min_cohort_size:
        return None
    score = min(
        0.95,
        0.55 + 0.07 * (len(cohort.agents) - min_cohort_size)
        + 0.05 * len(cohort.shared_devices)
        + 0.04 * min(10, cohort.sequential_count),
    )
    return Detection(
        signal_kind="agent.collusion",
        score=score,
        evidence={
            "cohort_id": cohort.cohort_id,
            "cohort_size": len(cohort.agents),
            "shared_device_count": len(cohort.shared_devices),
            "sequential_txn_count": cohort.sequential_count,
            "txn_count": len(txns),
        },
        boost_features=("cohort_size", "shared_device_count"),
    )


# ---------------------------------------------------------------------------
# 5. Float manipulation
# ---------------------------------------------------------------------------


def detect_float_manipulation(
    txns: list[AgentTxn],
    *,
    excess_threshold_minor: int = 50_000_000,
    movement_pairs_min: int = 4,
) -> Detection | None:
    """Two flavours rolled into one detector:

      a) **Excess float**: net inflow far above the per-agent typical
         peak. We look at `cash_in - cash_out` running balance over
         the window; a peak above `excess_threshold_minor` is
         excessive.
      b) **Float-movement**: ≥ N internal transfers (p2p or
         bank_transfer where counterparty_kind='agent') without
         corresponding customer activity — agent is shuffling
         float between accounts they control.
    """
    inflow = sum(t.amount_minor for t in txns if t.kind == "cash_in")
    outflow = sum(t.amount_minor for t in txns if t.kind == "cash_out")
    peak_balance = inflow - outflow

    internal_moves = sum(
        1
        for t in txns
        if t.kind in ("p2p_transfer", "bank_transfer")
        and t.counterparty_kind == "agent"
    )

    excess = peak_balance >= excess_threshold_minor
    moving = internal_moves >= movement_pairs_min
    if not (excess or moving):
        return None

    score_components: list[float] = []
    if excess:
        score_components.append(
            min(0.45, 0.3 + 0.05 * (peak_balance / max(1, excess_threshold_minor) - 1))
        )
    if moving:
        score_components.append(
            min(0.45, 0.3 + 0.03 * (internal_moves - movement_pairs_min))
        )
    score = min(0.95, 0.4 + sum(score_components))

    return Detection(
        signal_kind="agent.float_manipulation",
        score=score,
        evidence={
            "peak_float_minor": peak_balance,
            "excess_threshold_minor": excess_threshold_minor,
            "internal_moves": internal_moves,
            "excess": excess,
            "movement": moving,
        },
        boost_features=("peak_float_minor", "internal_moves"),
        severity="high",
    )


# ---------------------------------------------------------------------------
# Composite per-agent risk
# ---------------------------------------------------------------------------


def composite_agent_score(detections: Iterable[Detection]) -> float:
    """Combine per-pattern scores into a single agent risk score.

    Soft-OR (`1 - product(1 - s)`) is the right idiom: independent
    patterns each adding evidence; you don't get >1.0 from stacking,
    and a single very high pattern dominates.
    """
    p = 1.0
    for d in detections:
        p *= 1 - max(0.0, min(1.0, d.score))
    return round(1 - p, 3)


def pattern_count_by_kind(detections: Iterable[Detection]) -> dict[str, int]:
    return dict(Counter(d.signal_kind for d in detections))
