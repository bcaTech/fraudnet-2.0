"""Investigation report Pydantic model.

The LLM is told to return JSON in this exact shape (see
`prompt.SYSTEM_PROMPT`). We Pydantic-validate the response; parse
failures are explicitly surfaced to the analyst rather than swallowed
— a malformed report is itself a finding.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


Confidence = Literal["low", "medium", "high"]
Tier = Literal["tier2", "tier3"]


class EvidenceCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observation: str = Field(min_length=1, max_length=400)
    source: str = Field(min_length=1, max_length=200)


class RecommendedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tier: Tier
    action: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=400)


class InvestigationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=2_000)
    risk_assessment: str = Field(min_length=1, max_length=4_000)
    key_findings: list[str] = Field(default_factory=list, max_length=20)
    evidence_chain: list[EvidenceCitation] = Field(default_factory=list, max_length=40)
    recommended_actions: list[RecommendedAction] = Field(
        default_factory=list, max_length=10
    )
    data_gaps: list[str] = Field(default_factory=list, max_length=20)
    confidence: Confidence
    confidence_rationale: str = Field(min_length=1, max_length=2_000)


# The model occasionally wraps JSON in markdown fences (```json ... ```).
# Strip those defensively before we hand to Pydantic.
_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def parse_report(raw: str) -> InvestigationReport:
    """Parse LLM output into an `InvestigationReport`.

    Raises `ValueError` on parse failure — callers convert to a
    low-confidence stub report rather than letting the request fail.
    """
    cleaned = raw.strip()
    m = _FENCE_RE.match(cleaned)
    if m is not None:
        cleaned = m.group(1).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM returned non-JSON: {exc}") from exc
    try:
        return InvestigationReport.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"LLM JSON did not match schema: {exc}") from exc


def fallback_report(reason: str) -> InvestigationReport:
    """Build a low-confidence report when the LLM output cannot be parsed.

    Returned to the analyst with a clear note that the model output was
    malformed; the analyst can rerun the investigation.
    """
    return InvestigationReport(
        summary="LLM output could not be parsed into the required schema.",
        risk_assessment=(
            "The investigation agent received a response but it did not "
            "match the expected JSON schema. The analyst should rerun the "
            "investigation; if the failure persists, escalate to the "
            "platform team."
        ),
        key_findings=[],
        evidence_chain=[],
        recommended_actions=[],
        data_gaps=[f"Parse failure: {reason}"],
        confidence="low",
        confidence_rationale="Response was malformed.",
    )
