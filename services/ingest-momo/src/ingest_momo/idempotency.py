"""Idempotency dedup.

MoMo BSS retries on uncertainty — we may see the same event twice. We dedupe
on `event_id` (which is stable per (txn_id, event_type, timestamp_ms)) using
Redis SET NX with a TTL.

The implementation is pluggable: in-memory for unit tests, Redis for
production. Dedup is best-effort: a Redis outage falls open (allow the event
through) rather than fail-close (drop legitimate traffic) because losing a
MoMo event is worse than processing it twice. Downstream stream-graph and
stream-features tolerate duplicates by design.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from fraudnet.obs import counter, get_logger

_log = get_logger("ingest_momo.idempotency")

_DUPLICATES = counter(
    "ingest_momo_duplicates_total",
    "Idempotency-key duplicates suppressed at the webhook receiver.",
)
_FALLBACK_OPEN = counter(
    "ingest_momo_idempotency_fallback_open_total",
    "Times the idempotency cache failed open (allowed without dedup).",
)


class IdempotencyCache(ABC):
    @abstractmethod
    async def claim(self, key: str, *, ttl_s: int) -> bool:
        """Return True if the key was newly claimed; False if already seen."""

    @abstractmethod
    async def close(self) -> None: ...


class RedisIdempotencyCache(IdempotencyCache):
    def __init__(self, *, url: str, namespace: str = "momo:idem") -> None:
        import redis.asyncio as redis_async  # local import — only for prod path

        self._redis = redis_async.from_url(url, decode_responses=True)
        self._ns = namespace

    async def claim(self, key: str, *, ttl_s: int) -> bool:
        try:
            ok = await self._redis.set(f"{self._ns}:{key}", "1", nx=True, ex=ttl_s)
            if not ok:
                _DUPLICATES.inc()
                return False
            return True
        except Exception:  # noqa: BLE001 — fail open per docstring
            _FALLBACK_OPEN.inc()
            _log.warning("idempotency.fallback_open", key=key)
            return True

    async def close(self) -> None:
        await self._redis.close()


class InMemoryIdempotencyCache(IdempotencyCache):
    """Fixed-size in-memory cache for tests / single-process dev."""

    def __init__(self, max_entries: int = 100_000) -> None:
        self._seen: dict[str, None] = {}
        self._max = max_entries

    async def claim(self, key: str, *, ttl_s: int) -> bool:  # noqa: ARG002 — TTL ignored in-memory
        if key in self._seen:
            _DUPLICATES.inc()
            return False
        if len(self._seen) >= self._max:
            # Evict the oldest insertion. dict preserves insertion order.
            self._seen.pop(next(iter(self._seen)))
        self._seen[key] = None
        return True

    async def close(self) -> None:
        return None
