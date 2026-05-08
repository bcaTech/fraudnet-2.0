"""Buffered batch writer for the streaming graph mutation path.

CLAUDE.md §5.2: stream-graph "creates or updates nodes ... and adds edges
... via a buffered batch writer to Memgraph. Buffer size and flush cadence
are tuned for sub-minute consistency; never enable individual writes on the
hot path."

This is the abstraction stream-graph composes. Producers append GraphMutation
records; the writer flushes when either the buffer is full or the flush
interval has elapsed.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal

from fraudnet.graph.client import GraphClient, GraphScope
from fraudnet.obs import counter, get_logger

_log = get_logger("fraudnet.graph.batch")

_FLUSHED = counter(
    "fraudnet_graph_mutations_flushed_total",
    "Graph mutations flushed to Memgraph.",
    labelnames=("op",),
)
_DROPPED = counter(
    "fraudnet_graph_mutations_dropped_total",
    "Graph mutations dropped (queue full or flush failed).",
    labelnames=("reason",),
)


@dataclass(frozen=True)
class GraphMutation:
    op: Literal["upsert_node", "upsert_edge"]
    payload: dict[str, Any] = field(default_factory=dict)


class BufferedGraphWriter:
    def __init__(
        self,
        client: GraphClient,
        scope: GraphScope,
        *,
        max_buffer: int = 1000,
        flush_interval_s: float = 5.0,
    ) -> None:
        self._client = client
        self._scope = scope
        self._max = max_buffer
        self._interval = flush_interval_s
        self._buf: list[GraphMutation] = []
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="graph-batch-writer")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
        await self.flush()

    async def append(self, mutation: GraphMutation) -> None:
        async with self._lock:
            if len(self._buf) >= self._max:
                _DROPPED.labels(reason="buffer_full").inc()
                _log.warning("graph_writer.buffer_full", buffer=len(self._buf))
                return
            self._buf.append(mutation)
            if len(self._buf) >= self._max:
                # Trigger a flush opportunistically; do not await.
                asyncio.create_task(self.flush(), name="graph-flush-opportunistic")

    async def flush(self) -> None:
        async with self._lock:
            if not self._buf:
                return
            batch, self._buf = self._buf, []
        try:
            async with self._client.session(self._scope) as session:
                for m in batch:
                    if m.op == "upsert_node":
                        await session._run(  # noqa: SLF001 — internal usage by intent
                            "batch_upsert_node",
                            """
                            MERGE (n {tenant_id: $tenant_id, id: $id})
                            ON CREATE SET n.created_at = timestamp()
                            SET n += $properties, n.updated_at = timestamp()
                            """,
                            tenant_id=self._scope.tenant_id,
                            id=m.payload["id"],
                            properties=m.payload.get("properties", {}),
                        )
                    elif m.op == "upsert_edge":
                        await session._run(  # noqa: SLF001
                            "batch_upsert_edge",
                            """
                            MATCH (a {tenant_id: $tenant_id, id: $src})
                            MATCH (b {tenant_id: $tenant_id, id: $dst})
                            CREATE (a)-[r:RELATED]->(b)
                            SET r += $properties
                            """,
                            tenant_id=self._scope.tenant_id,
                            src=m.payload["src"],
                            dst=m.payload["dst"],
                            properties=m.payload.get("properties", {}),
                        )
                    _FLUSHED.labels(op=m.op).inc()
        except Exception:  # noqa: BLE001 — robust flush; alerts surface via metrics
            _DROPPED.labels(reason="flush_failed").inc()
            _log.exception("graph_writer.flush_failed", batch_size=len(batch))

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except TimeoutError:
                await self.flush()
