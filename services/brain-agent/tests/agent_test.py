"""Agent end-to-end with stubbed evidence + LLM."""

from __future__ import annotations

import json

from brain_agent.agent import InvestigationAgent, JobStore
from brain_agent.llm import RecordingStubLLMClient
from brain_agent.prompt import EvidencePackage


_VALID_REPORT = json.dumps(
    {
        "summary": "Test summary.",
        "risk_assessment": "Test risk assessment.",
        "key_findings": ["finding 1"],
        "evidence_chain": [
            {"observation": "vel_1m=47", "source": "feature_snapshots.NUM_abc.vel_1m"}
        ],
        "recommended_actions": [
            {"tier": "tier3", "action": "investigate", "rationale": "high risk"}
        ],
        "data_gaps": [],
        "confidence": "high",
        "confidence_rationale": "All sources available.",
    }
)


async def _make_evidence() -> EvidencePackage:
    return EvidencePackage(
        target_kind="alert",
        target_id="00000000-0000-0000-0000-000000000001",
        redacted_target="ALERT_abcdef01",
        alert={"id": "abc", "severity": "high"},
        not_available=["prior_decisions"],
    )


async def test_agent_happy_path() -> None:
    llm = RecordingStubLLMClient(response_text=_VALID_REPORT)
    store = JobStore()
    agent = InvestigationAgent(llm=llm, store=store)
    job = await agent.submit(
        analyst_id="user-1",
        tenant_id="mtn-ghana",
        target_kind="alert",
        target_id="00000000-0000-0000-0000-000000000001",
        evidence_factory=_make_evidence,
    )
    assert job.status == "completed"
    assert job.report is not None
    assert job.report.confidence == "high"
    assert job.redacted_target == "ALERT_abcdef01"
    # The agent calls the LLM exactly once
    assert len(llm.calls) == 1


async def test_agent_falls_back_on_parse_failure() -> None:
    """A malformed LLM response yields a low-confidence stub report
    rather than failing the request."""
    llm = RecordingStubLLMClient(response_text="not a json blob")
    store = JobStore()
    agent = InvestigationAgent(llm=llm, store=store)
    job = await agent.submit(
        analyst_id="user-1",
        tenant_id="mtn-ghana",
        target_kind="alert",
        target_id="aid",
        evidence_factory=_make_evidence,
    )
    assert job.status == "completed"
    assert job.report is not None
    assert job.report.confidence == "low"
    assert job.report.data_gaps  # populated with parse-failure note


async def test_agent_records_evidence_failure_as_failed() -> None:
    async def _bad_factory() -> EvidencePackage:
        raise RuntimeError("postgres down")

    llm = RecordingStubLLMClient(response_text=_VALID_REPORT)
    store = JobStore()
    agent = InvestigationAgent(llm=llm, store=store)
    job = await agent.submit(
        analyst_id="user-1",
        tenant_id="mtn-ghana",
        target_kind="alert",
        target_id="aid",
        evidence_factory=_bad_factory,
    )
    assert job.status == "failed"
    assert job.error and "postgres down" in job.error


async def test_job_store_round_trip() -> None:
    """In-memory store survives put → get with full report fidelity."""
    llm = RecordingStubLLMClient(response_text=_VALID_REPORT)
    store = JobStore()
    agent = InvestigationAgent(llm=llm, store=store)
    job = await agent.submit(
        analyst_id="user-1",
        tenant_id="mtn-ghana",
        target_kind="alert",
        target_id="aid",
        evidence_factory=_make_evidence,
    )
    fetched = await store.get(job.job_id)
    assert fetched is not None
    assert fetched.status == "completed"
    assert fetched.report is not None
    assert fetched.report.confidence == "high"
