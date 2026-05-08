"""Investigation agent — orchestrates evidence → LLM → report.

The agent is purely composition: collect evidence, build prompt, call
the LLM, parse the response. No detection, no decisions.

Job lifecycle:
  pending → running → completed | failed

The job state is held in Redis (Phase 1 keeps it simple; Phase 2 will
move to Postgres + Iceberg for long retention). Jobs are addressable by
job_id; the analyst polls `/investigate/{job_id}` for the final report.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from time import time
from typing import Any
from uuid import UUID, uuid4

from fraudnet.obs import counter, get_logger

from brain_agent.llm import LLMClient, LLMResponse
from brain_agent.prompt import EvidencePackage, SYSTEM_PROMPT, render_user_prompt
from brain_agent.report import InvestigationReport, fallback_report, parse_report

_log = get_logger("brain_agent.agent")

_JOB_OUTCOMES = counter(
    "brain_agent_jobs_total",
    "Investigation jobs by outcome.",
    labelnames=("target_kind", "outcome"),
)


@dataclass
class Job:
    job_id: str
    analyst_id: str
    tenant_id: str
    target_kind: str       # 'alert' | 'ring' | 'entity'
    target_id: str
    status: str            # 'pending' | 'running' | 'completed' | 'failed'
    created_at_ms: int
    updated_at_ms: int
    redacted_target: str | None = None
    report: InvestigationReport | None = None
    error: str | None = None
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    llm_cache_read_tokens: int = 0
    not_available: list[str] = field(default_factory=list)


def new_job(
    *, analyst_id: str, tenant_id: str, target_kind: str, target_id: str
) -> Job:
    now_ms = int(time() * 1000)
    return Job(
        job_id=f"inv_{uuid4().hex[:24]}",
        analyst_id=analyst_id,
        tenant_id=tenant_id,
        target_kind=target_kind,
        target_id=target_id,
        status="pending",
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
    )


# ---------------------------------------------------------------------------
# Job store
# ---------------------------------------------------------------------------


class JobStore:
    """Redis-backed job state. Falls back to an in-memory dict when
    Redis is unavailable so dev / tests do not require it."""

    def __init__(self, *, redis: Any | None = None, ttl_s: int = 86_400 * 7) -> None:
        self._redis = redis
        self._mem: dict[str, Job] = {}
        self._ttl_s = ttl_s

    async def put(self, job: Job) -> None:
        if self._redis is None:
            self._mem[job.job_id] = job
            return
        payload = _serialise(job)
        try:
            await self._redis.setex(_key(job.job_id), self._ttl_s, payload)
        except Exception:  # noqa: BLE001
            _log.warning("brain_agent.jobstore.redis_unavailable", job_id=job.job_id)
            self._mem[job.job_id] = job

    async def get(self, job_id: str) -> Job | None:
        if self._redis is not None:
            try:
                raw = await self._redis.get(_key(job_id))
            except Exception:  # noqa: BLE001
                raw = None
            if raw:
                return _deserialise(raw if isinstance(raw, str) else raw.decode())
        return self._mem.get(job_id)


def _key(job_id: str) -> str:
    return f"brain_agent:job:{job_id}"


def _serialise(job: Job) -> str:
    raw = asdict(job)
    if job.report is not None:
        raw["report"] = job.report.model_dump()
    return json.dumps(raw)


def _deserialise(payload: str) -> Job:
    raw = json.loads(payload)
    report_raw = raw.pop("report", None)
    job = Job(**raw)
    if report_raw is not None:
        job.report = InvestigationReport.model_validate(report_raw)
    return job


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class InvestigationAgent:
    """Composes evidence collection + LLM call + parse."""

    def __init__(
        self,
        *,
        llm: LLMClient,
        store: JobStore,
        max_concurrent: int = 4,
    ) -> None:
        self._llm = llm
        self._store = store
        self._sem = asyncio.Semaphore(max_concurrent)

    async def submit(
        self,
        *,
        analyst_id: str,
        tenant_id: str,
        target_kind: str,
        target_id: str,
        evidence_factory,  # callable () -> Awaitable[EvidencePackage]
    ) -> Job:
        """Create a job and run it inline.

        We deliberately do not background the request — the LLM call is
        the dominant latency and the analyst is already waiting on it.
        Async-polling clients can use the job_id to refresh; the first
        response also carries the completed job.
        """
        job = new_job(
            analyst_id=analyst_id,
            tenant_id=tenant_id,
            target_kind=target_kind,
            target_id=target_id,
        )
        await self._store.put(job)

        async with self._sem:
            try:
                evidence = await evidence_factory()
            except Exception as exc:  # noqa: BLE001
                _log.exception("brain_agent.evidence_failed", job_id=job.job_id)
                _JOB_OUTCOMES.labels(target_kind=target_kind, outcome="evidence_error").inc()
                job.status = "failed"
                job.error = f"evidence collection failed: {exc}"
                job.updated_at_ms = int(time() * 1000)
                await self._store.put(job)
                return job

            job.status = "running"
            job.redacted_target = evidence.redacted_target
            job.not_available = list(evidence.not_available)
            job.updated_at_ms = int(time() * 1000)
            await self._store.put(job)

            try:
                response = await self._llm.complete(
                    system=SYSTEM_PROMPT,
                    user=render_user_prompt(evidence),
                )
            except Exception as exc:  # noqa: BLE001
                _log.exception("brain_agent.llm_failed", job_id=job.job_id)
                _JOB_OUTCOMES.labels(target_kind=target_kind, outcome="llm_error").inc()
                job.status = "failed"
                job.error = f"LLM call failed: {exc}"
                job.updated_at_ms = int(time() * 1000)
                await self._store.put(job)
                return job

            job.llm_input_tokens = response.input_tokens
            job.llm_output_tokens = response.output_tokens
            job.llm_cache_read_tokens = response.cache_read_tokens

            try:
                report = parse_report(response.text)
                outcome = "ok"
            except ValueError as exc:
                _log.warning(
                    "brain_agent.report_parse_failed",
                    job_id=job.job_id,
                    error=str(exc),
                )
                report = fallback_report(str(exc))
                outcome = "parse_error"

            job.report = report
            job.status = "completed"
            job.updated_at_ms = int(time() * 1000)
            await self._store.put(job)
            _JOB_OUTCOMES.labels(target_kind=target_kind, outcome=outcome).inc()
            return job
