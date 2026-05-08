"""Report parsing — happy path, fenced JSON, malformed."""

from __future__ import annotations

import json

import pytest

from brain_agent.report import fallback_report, parse_report


_VALID = {
    "summary": "Caller exhibits velocity-burst pattern.",
    "risk_assessment": "The number's 1m velocity is 47 calls vs baseline 8.",
    "key_findings": ["high velocity", "fanout > 50"],
    "evidence_chain": [
        {"observation": "vel_1m=47", "source": "feature_snapshots.NUM_abc.vel_1m"}
    ],
    "recommended_actions": [
        {"tier": "tier2", "action": "send fraud_alert SMS", "rationale": "high score"}
    ],
    "data_gaps": ["prior_decisions"],
    "confidence": "medium",
    "confidence_rationale": "Subgraph was not available.",
}


def test_parse_valid_report() -> None:
    report = parse_report(json.dumps(_VALID))
    assert report.confidence == "medium"
    assert len(report.key_findings) == 2


def test_parse_handles_fenced_json() -> None:
    """Models occasionally wrap output in ```json ... ```."""
    fenced = "```json\n" + json.dumps(_VALID) + "\n```"
    report = parse_report(fenced)
    assert report.confidence == "medium"


def test_parse_rejects_non_json() -> None:
    with pytest.raises(ValueError, match="non-JSON"):
        parse_report("not even close to json")


def test_parse_rejects_wrong_schema() -> None:
    with pytest.raises(ValueError, match="schema"):
        parse_report(json.dumps({"summary": "missing required fields"}))


def test_parse_rejects_invalid_confidence() -> None:
    bad = dict(_VALID)
    bad["confidence"] = "extreme"
    with pytest.raises(ValueError):
        parse_report(json.dumps(bad))


def test_parse_rejects_tier1_recommendation() -> None:
    """Tier 1 must never come from the agent — only the decisions
    service can dispatch inline. Schema enforces this."""
    bad = dict(_VALID)
    bad["recommended_actions"] = [
        {"tier": "tier1", "action": "block now", "rationale": "obviously"}
    ]
    with pytest.raises(ValueError):
        parse_report(json.dumps(bad))


def test_fallback_report_is_low_confidence() -> None:
    fb = fallback_report("schema mismatch")
    assert fb.confidence == "low"
    assert fb.recommended_actions == []
    assert any("schema mismatch" in g for g in fb.data_gaps)
