"""Feature contribution ranking + baselines.

Two responsibilities:
  - Compute per-feature weights from a scoring result + the rule that
    fired. The scorer hands us the evidence dict and the signal_kind;
    we map back to which features actually drove the threshold.
  - Resolve a baseline for each feature so the explanation can say
    "vel_1m=47 vs baseline p95=8".

Baselines are pluggable (`BaselineProvider`). Phase 1 ships the
`StaticBaselineProvider` — published-once, hand-tuned numbers matching
the heuristic thresholds. Phase 2 swaps in a `RollingBaselineProvider`
backed by a feature-cohort store.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

from fraudnet.schemas import FeatureContribution


# ---------------------------------------------------------------------------
# Baseline resolution
# ---------------------------------------------------------------------------


class BaselineProvider(Protocol):
    def baseline(self, feature: str) -> float | None: ...


# Hand-tuned cohort baselines for the Phase 1 heuristic features. Numbers
# come from the same Airtel-style reference profile that drove the
# scorer thresholds (CLAUDE.md §5.3 / DECISIONS.md D-006); they are
# "what a non-fraudulent number looks like at p95 over 24h".
_PHASE_1_BASELINES: dict[str, float] = {
    # Voice / number
    "vel_1m": 1,
    "vel_5m": 4,
    "vel_1h": 30,
    "fanout_1h": 8,
    "imei_count": 1,
    "sms_freq_1h": 5,
    # Wallet
    "txn_velocity_1h": 3,
    "counterparty_diversity_24h": 4,
    "value_p95_24h": 20_000,  # GHS minor units (pesewas) — ~GHS 200
    # Geo / temporal
    "geo_entropy": 0.4,
    "inter_call_p95_s": 600,
}


@dataclass(frozen=True)
class StaticBaselineProvider:
    """Static baselines. Reads from a frozen dict so callers can override
    in tests without touching the module-level default."""

    baselines: dict[str, float]

    @classmethod
    def default(cls) -> "StaticBaselineProvider":
        return cls(baselines=dict(_PHASE_1_BASELINES))

    def baseline(self, feature: str) -> float | None:
        return self.baselines.get(feature)


# ---------------------------------------------------------------------------
# Contribution computation
# ---------------------------------------------------------------------------


def _coerce_float(v: object) -> float | None:
    if isinstance(v, bool):
        # Treat booleans as 1.0/0.0 — `rcs_verified_recent` is the canonical
        # case. Without this the contribution disappears from the ranking.
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    return None


def contributions_from_evidence(
    evidence: dict[str, object],
    *,
    baselines: BaselineProvider,
    boost_features: Iterable[str] = (),
) -> list[FeatureContribution]:
    """Convert the scorer's evidence dict to a list of contributions.

    `boost_features` names features that the rule which fired
    *specifically* keyed on; these get their weight bumped so the
    ranking surfaces them above merely-elevated other features. Each
    boost adds a multiplicative 1.5× before clamping.

    Weights are normalised against the baseline using `(value - baseline) /
    max(baseline, 1)`, clamped to [-1, 1]. Missing baselines fall back to
    a heuristic-friendly 1.0 (so the contribution is still surfaced).
    """
    boost_set = set(boost_features)
    out: list[FeatureContribution] = []

    for key, raw in evidence.items():
        value = _coerce_float(raw)
        if value is None:
            continue
        base = baselines.baseline(key)
        if base is None:
            # Unknown feature — surface but with a low weight so the
            # ranked top-N still includes it if nothing else is loud.
            weight = min(1.0, abs(value) / 100.0)
        else:
            denom = max(abs(base), 1.0)
            weight = (value - base) / denom
        if key in boost_set:
            weight *= 1.5
        weight = max(-1.0, min(1.0, weight))
        out.append(
            FeatureContribution(
                feature=key, value=value, baseline=base, weight=weight
            )
        )
    return out


def rank_contributions(
    contributions: list[FeatureContribution], *, top_n: int = 3
) -> list[FeatureContribution]:
    """Sort by |weight| desc, return the top N."""
    return sorted(contributions, key=lambda c: abs(c.weight), reverse=True)[:top_n]
