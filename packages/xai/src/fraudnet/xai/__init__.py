"""Explainable AI layer.

Every SignalEventV1 carries:
  - `feature_contributions` — top-N (feature, value, baseline, weight)
    tuples ranked by absolute weight.
  - `explanation_text` — a one-sentence human-readable summary tying
    the score to the dominant features.

This package owns the canonical functions that produce both. Brain
services (`brain-behavioural`, `brain-content`) call them at the same
seam as `to_signal()` so every published signal is explainable.

Design principles:
  - **No PII.** Explanations reference entity *kinds* and feature
    *names*, never raw values like MSISDNs. The `redact_*` family in
    `fraudnet.obs.redact` handles unstructured strings if a caller
    needs them.
  - **Deterministic.** Given the same scoring result, the same
    explanation comes out. Tests rely on this.
  - **Cheap.** No model inference; pure-Python ranking + template
    rendering. Adds <1ms per signal — Tier-1 latency budget is intact.
"""

from fraudnet.xai.contributions import (
    BaselineProvider,
    StaticBaselineProvider,
    contributions_from_evidence,
    rank_contributions,
)
from fraudnet.xai.explanations import (
    explain_content_signal,
    explain_signal,
    feature_label,
    summarize_anomalies,
)
from fraudnet.xai.pattern_match import PatternMatch, score_pattern_match
from fraudnet.xai.persistence import xai_block_for_signal

__all__ = [
    "BaselineProvider",
    "PatternMatch",
    "StaticBaselineProvider",
    "contributions_from_evidence",
    "explain_content_signal",
    "explain_signal",
    "feature_label",
    "rank_contributions",
    "score_pattern_match",
    "summarize_anomalies",
    "xai_block_for_signal",
]
