"""Content-classifier match details.

`brain-content` works on text (SMS bodies, URLs) rather than feature
vectors. The XAI surface for content is "which pattern matched + which
terms within the body / URL triggered it". No raw PII — public scam
template fragments are fine to surface to customers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fraudnet.schemas import FeatureContribution


@dataclass(frozen=True)
class PatternMatch:
    """A pattern (rule, classifier label) that fired with provenance."""

    pattern_id: str          # e.g. 'smishing.prize_claim_template'
    pattern_label: str       # human label, e.g. 'Prize-claim template'
    score: float             # in [0, 1]
    matched_terms: tuple[str, ...] = field(default_factory=tuple)
    domain: str | None = None


def score_pattern_match(
    match: PatternMatch,
) -> list[FeatureContribution]:
    """Convert a content pattern match to FeatureContributions.

    The XAI shape for content is uniform across kinds:
      - `pattern.<id>`        weight = score, value = 1.0
      - `term.<term>`         weight = score / N, value = 1.0
      - `domain.<domain>`     weight = score, value = 1.0 (when present)

    This lets api-noc render content explanations in the same UI as
    behavioural ones — the XAI tab is identical.
    """
    out: list[FeatureContribution] = [
        FeatureContribution(
            feature=f"pattern.{match.pattern_id}",
            value=1.0,
            baseline=0.0,
            weight=max(-1.0, min(1.0, match.score)),
        )
    ]
    if match.matched_terms:
        per_term = max(-1.0, min(1.0, match.score / max(1, len(match.matched_terms))))
        for term in match.matched_terms[:5]:
            out.append(
                FeatureContribution(
                    feature=f"term.{_safe(term)}",
                    value=1.0,
                    baseline=0.0,
                    weight=per_term,
                )
            )
    if match.domain:
        out.append(
            FeatureContribution(
                feature=f"domain.{_safe(match.domain)}",
                value=1.0,
                baseline=0.0,
                weight=max(-1.0, min(1.0, match.score)),
            )
        )
    return out


def _safe(text: str) -> str:
    """Truncate + sanitise feature names. Feature names are size-bounded
    by the schema (max 64 chars) and should be ASCII-safe."""
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in text)
    return cleaned[:48]
