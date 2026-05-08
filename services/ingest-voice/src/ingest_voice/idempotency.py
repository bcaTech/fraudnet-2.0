"""Re-uses the same idempotency-cache primitives as ingest-momo.

We could share via a package, but the cache key namespacing is service-
specific (different TTL semantics for voice vs MoMo). Keep it co-located.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from fraudnet.obs import counter, get_logger

_log = get_logger("ingest_voice.idempotency")

_DUPLICATES = counter(
    "ingest_voice_duplicates_total",
    "Idempotency-key duplicates suppressed at the webhook receiver.",
)
_FALLBACK_OPEN = counter(
    "ingest_voice_idempotency_fallback_open_total",
    "Times the idempotency cache failed open (allowed without dedup).",
)


class IdempotencyCache(ABC):
    @abstractmethod
    async def claim(self, key: str, *, ttl_s: int) -> bool: ...

    @abstractmethod
    async def close(self) -> None: ...


class RedisIdempotencyCache(IdempotencyCache):
    def __init__(self, *, url: str, namespace: str = "voice:idem") -> None:
        import redis.asyncio as redis_async

        self._redis = redis_async.from_url(url, decode_responses=True)
        self._ns = namespace

    async def claim(self, key: str, *, ttl_s: int) -> bool:
        try:
            ok = await self._redis.set(f"{self._ns}:{key}", "1", nx=True, ex=ttl_s)
            if not ok:
                _DUPLICATES.inc()
                return False
            return True
        except Exception:  # noqa: BLE001 — fail open
            _FALLBACK_OPEN.inc()
            _log.warning("idempotency.fallback_open", key=key)
            return True

    async def close(self) -> None:
        await self._redis.close()


class InMemoryIdempotencyCache(IdempotencyCache):
    def __init__(self, max_entries: int = 100_000) -> None:
        self._seen: dict[str, None] = {}
        self._max = max_entries

    async def claim(self, key: str, *, ttl_s: int) -> bool:  # noqa: ARG002
        if key in self._seen:
            _DUPLICATES.inc()
            return False
        if len(self._seen) >= self._max:
            self._seen.pop(next(iter(self._seen)))
        self._seen[key] = None
        return True

    async def close(self) -> None:
        return None
