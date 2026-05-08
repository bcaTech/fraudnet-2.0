"""Export-job tracking.

A regulator export takes seconds-to-minutes for a busy month. We
respond to POST with a `job_id` and let the client poll
GET /compliance/export/{job_id}. Jobs live in-process (Phase 2 swaps
to Postgres for cross-replica visibility).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class ExportJob:
    job_id: str
    regulator: str
    period_start: str
    period_end: str
    tenant_id: str
    status: str       # pending | running | completed | failed
    created_at_ms: int
    updated_at_ms: int
    actor_id: str | None = None
    json_payload: dict[str, Any] | None = None
    pdf_bytes: bytes | None = None
    review_field_count: int = 0
    error: str | None = None


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, ExportJob] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        *,
        regulator: str,
        period_start: str,
        period_end: str,
        tenant_id: str,
        actor_id: str | None,
    ) -> ExportJob:
        now = int(time.time() * 1000)
        job = ExportJob(
            job_id=f"exp_{uuid4().hex[:24]}",
            regulator=regulator,
            period_start=period_start,
            period_end=period_end,
            tenant_id=tenant_id,
            status="pending",
            created_at_ms=now,
            updated_at_ms=now,
            actor_id=actor_id,
        )
        async with self._lock:
            self._jobs[job.job_id] = job
        return job

    async def update(self, job: ExportJob) -> None:
        async with self._lock:
            job.updated_at_ms = int(time.time() * 1000)
            self._jobs[job.job_id] = job

    async def get(self, job_id: str) -> ExportJob | None:
        async with self._lock:
            return self._jobs.get(job_id)
