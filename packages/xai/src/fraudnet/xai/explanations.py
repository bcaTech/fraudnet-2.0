"""Human-readable explanation generators.

`explain_signal()` produces one sentence per (signal_kind, top
contributions) tuple. The text is designed for two audiences:
  1. The NOC analyst skimming a list of alerts.
  2. The end-user customer (when the signal eventually surfaces in a
     fraud-alert SMS) — kept to short, plain-language sentences.

No PII. The explanation references entity *kinds* (caller, wallet,
device) and feature *names* (call velocity, IMEI churn) — never raw
identifiers.
"""

from __future__ import annotations

from fraudnet.schemas import FeatureContribution


# Human labels for the heuristic features. Keys must match
# `contributions_from_evidence` keys.
_FEATURE_LABELS: dict[str, str] = {
    "vel_1m": "calls in the last minute",
    "vel_5m": "calls in the last 5 minutes",
    "vel_1h": "calls in the last hour",
    "fanout_1h": "distinct callees in the last hour",
    "imei_count": "distinct devices used in 30 days",
    "sms_freq_1h": "SMS sent in the last hour",
    "smshash_top": "repeated SMS template",
    "txn_velocity_1h": "wallet transactions per hour",
    "counterparty_diversity_24h": "distinct counterparties in 24h",
    "value_p95_24h": "high-value transactions",
    "geo_entropy": "geographic motion entropy",
    "inter_call_p95_s": "inter-call gap",
    "rcs_verified_recent": "RCS-verified-sender flag",
}


def feature_label(name: str) -> str:
    """Pretty label for a feature. Falls back to the raw name."""
    return _FEATURE_LABELS.get(name, name)


# Per-signal sentence templates. `{phrase}` is the rendered top-contribution
# phrase from `summarize_anomalies`; `{score}` is the model score. Adding
# a new signal_kind without a template falls back to the generic shape.
_TEMPLATES: dict[str, str] = {
    "voice.velocity_burst": (
        "Voice velocity burst — {phrase}. "
        "Score {score:.2f}; consistent with wangiri or robocall behaviour."
    ),
    "device.imei_churn": (
        "Device churn on this number — {phrase}. "
        "Score {score:.2f}; consistent with SIM-swap or compromised handset."
    ),
    "sms.bulk_template": (
        "SMS bulk-template send — {phrase}. "
        "Score {score:.2f}; consistent with smishing operator."
    ),
    "momo.mule_velocity": (
        "Wallet activity surge — {phrase}. "
        "Score {score:.2f}; consistent with mule-account behaviour."
    ),
    "momo.high_value_velocity": (
        "High-value wallet velocity — {phrase}. "
        "Score {score:.2f}; consistent with cash-out arbitrage."
    ),
}

_GENERIC_TEMPLATE = "{kind}: {phrase}. Score {score:.2f}."


def summarize_anomalies(top: list[FeatureContribution]) -> str:
    """Render the top contributions as a comma-separated phrase.

    Example output:
      "calls in the last minute = 47 (baseline ~1), distinct callees in
       the last hour = 88 (baseline ~8)"
    """
    parts: list[str] = []
    for c in top:
        label = feature_label(c.feature)
        if c.baseline is not None:
            parts.append(
                f"{label} = {_fmt(c.value)} (baseline ~{_fmt(c.baseline)})"
            )
        else:
            parts.append(f"{label} = {_fmt(c.value)}")
    if not parts:
        return "no individual feature stood out"
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + ", and " + parts[-1]


def explain_signal(
    *,
    signal_kind: str,
    score: float,
    top_contributions: list[FeatureContribution],
) -> str:
    """One-sentence explanation for a behavioural signal."""
    template = _TEMPLATES.get(signal_kind, _GENERIC_TEMPLATE)
    phrase = summarize_anomalies(top_contributions)
    return template.format(kind=signal_kind, phrase=phrase, score=score)


def explain_content_signal(
    *,
    signal_kind: str,
    score: float,
    pattern_label: str,
    matched_terms: list[str] | None = None,
    domain: str | None = None,
) -> str:
    """Content-side explanation. Different shape: there's no feature
    vector — the evidence is "which pattern matched + which terms".

    `matched_terms` and `domain` are *not* PII (they're public scam-
    template fragments, e.g. "claim your prize"). The explanation
    is safe to surface to the customer.
    """
    pieces = [f"Pattern: {pattern_label}"]
    if matched_terms:
        head = ", ".join(f'"{t}"' for t in matched_terms[:3])
        pieces.append(f"matched terms: {head}")
    if domain:
        pieces.append(f"domain: {domain}")
    base = "; ".join(pieces)
    return f"{signal_kind}: {base}. Score {score:.2f}."


def _fmt(value: float) -> str:
    """Compact numeric formatting — integer if whole, 2 decimals otherwise."""
    if abs(value - round(value)) < 1e-6:
        return str(int(round(value)))
    return f"{value:.2f}"
