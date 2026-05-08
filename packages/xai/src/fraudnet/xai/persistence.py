"""Convert SignalEventV1 XAI fields → alerts.details.xai block.

The alert persister (decisions service or its replacement) calls
`xai_block_for_signal()` to derive the `details.xai` JSON that the
api-noc detail view surfaces. The two sides of the wire are decoupled
through this helper so future model changes don't require coordinated
edits across services.
"""

from __future__ import annotations

from typing import Any

from fraudnet.schemas import FeatureContribution, SignalEventV1


def xai_block_for_signal(signal: SignalEventV1) -> dict[str, Any] | None:
    """Build the `details.xai` payload from a signal's XAI fields.

    Returns None when the signal carries no explanation — older or
    third-party signals without XAI should not pollute alert details
    with empty blocks.
    """
    if signal.explanation_text is None and not signal.feature_contributions:
        return None
    return {
        "explanation_text": signal.explanation_text,
        "top_features": [_contribution_dict(c) for c in signal.feature_contributions],
        "model_id": signal.score.model_id,
        "model_version": signal.score.model_version,
    }


def _contribution_dict(c: FeatureContribution) -> dict[str, Any]:
    return {
        "feature": c.feature,
        "value": c.value,
        "baseline": c.baseline,
        "weight": c.weight,
    }
