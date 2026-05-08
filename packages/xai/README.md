# fraudnet-xai

Explainability layer for fraud signals. Every `SignalEventV1` published
by `brain-behavioural` and `brain-content` carries:

- `feature_contributions` — top-N (feature, value, baseline, weight)
  ranked by absolute weight.
- `explanation_text` — one-sentence human-readable summary.

## API

```python
from fraudnet.xai import (
    StaticBaselineProvider,
    contributions_from_evidence,
    explain_signal,
    rank_contributions,
)

baselines = StaticBaselineProvider.default()
contribs = contributions_from_evidence(
    {"vel_1m": 47, "fanout_1h": 88},
    baselines=baselines,
    boost_features=("vel_1m", "fanout_1h"),
)
top = rank_contributions(contribs, top_n=3)
explanation = explain_signal(
    signal_kind="voice.velocity_burst",
    score=0.92,
    top_contributions=top,
)
# "Voice velocity burst — calls in the last minute = 47 (baseline ~1),
#  and distinct callees in the last hour = 88 (baseline ~8). Score 0.92;
#  consistent with wangiri or robocall behaviour."
```

For content-side signals (`brain-content`):

```python
from fraudnet.xai import PatternMatch, explain_content_signal, score_pattern_match

match = PatternMatch(
    pattern_id="sms.template_smishing",
    pattern_label="Smishing template",
    score=0.85,
    matched_terms=("claim your prize", "click here"),
    domain="evil.example",
)
contribs = score_pattern_match(match)  # → FeatureContribution list
explanation = explain_content_signal(
    signal_kind="sms.template_smishing",
    score=0.85,
    pattern_label="Smishing template",
    matched_terms=list(match.matched_terms),
    domain=match.domain,
)
```

## PII rules

- Explanations and contributions reference entity *kinds* and feature
  *names*, never raw values like MSISDNs.
- Pattern fragments (matched terms, domains) are public scam-template
  text and are safe to surface to customers.
- The Pydantic schema for `FeatureContribution` (in
  `fraudnet.schemas.signals`) does not accept fields named after PII
  identifiers; if you find yourself wanting to add one, use a redaction
  layer first.

## Determinism

Given the same `(signal_kind, score, top_contributions)`, `explain_signal()`
returns the exact same string. This is a wire-format guarantee — tests
across services lean on it.

## Cost

Pure-Python ranking + template substitution. Adds <1 ms to the signal
emission path. No model inference, no I/O. Safe for the Tier-1 inline
budget.

## Persistence

`xai_block_for_signal(signal) -> dict | None` produces the JSON blob
that the alert persister stores under `alerts.details.xai`. The api-noc
detail view reads from there and renders the XAI tab.
