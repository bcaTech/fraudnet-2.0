"""Scheduled-batch runner.

Wakes every `interval_s` seconds and triggers `Analyzer.run_once()`. The
analyser is reentrant-safe (it uses its own session) but we serialise
batches to avoid duplicate motif emission on overlapping runs.
"""

from __future__ import annotations

import asyncio

from fraudnet.kafka import KafkaSettings
from fraudnet.obs import counter, get_logger

from brain_graph.analyzer import Analyzer

_log = get_logger("brain_graph.runner")

_BATCH_RUNS = counter(
    "brain_graph_batch_runs_total",
    "Scheduled brain-graph batch runs.",
    labelnames=("outcome",),
)


class BatchScheduler:
    def __init__(self, *, analyzer: Analyzer, interval_s: int) -> None:
        self._analyzer = analyzer
        self._interval_s = interval_s
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        # Initial delay so the service is healthy before the first scrape.
        await asyncio.sleep(min(30, self._interval_s))
        while not self._stop.is_set():
            await self.trigger()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_s)
            except asyncio.TimeoutError:
                continue

    async def trigger(self) -> None:
        if self._lock.locked():
            _log.info("brain_graph.batch_skipped_overlap")
            _BATCH_RUNS.labels(outcome="skipped").inc()
            return
        async with self._lock:
            try:
                await self._analyzer.run_once()
                _BATCH_RUNS.labels(outcome="ok").inc()
            except Exception as exc:  # noqa: BLE001
                _log.error("brain_graph.batch_failed", error=str(exc))
                _BATCH_RUNS.labels(outcome="error").inc()

    async def stop(self) -> None:
        self._stop.set()


def make_settings_factory(*, bootstrap: str, schema_registry_url: str, group_id: str):
    def factory(client_id: str) -> KafkaSettings:
        return KafkaSettings(
            bootstrap_servers=bootstrap,
            schema_registry_url=schema_registry_url,
            client_id=client_id,
            group_id=group_id,
        )

    return factory
