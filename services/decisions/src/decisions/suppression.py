"""Suppression dedup.

A decision is suppressed if its (suppression_key, action, tier) tuple has
been emitted within the rule's suppression_window_s. Implemented as a Redis
SET-NX with TTL; in-memory variant for tests.

The suppression key is set by the producer of the signal/motif; we
namespace it with the action so different actions on the same subject
don't suppress each other.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from fraudnet.obs import counter, get_logger

_log = get_logger("decisions.suppression")

_SUPPRESSED = counter(
    "decisions_suppressed_total",
    "Decisions suppressed by deduplication.",
    labelnames=("tier", "action"),
)
_FALLBACK_OPEN = counter(
    "decisions_suppression_fallback_open_total",
    "Suppression check failed open (Redis unreachable).",
)


class SuppressionStore(ABC):
    @abstractmethod
    async def claim(self, key: str, *, ttl_s: int) -> bool: ...

    @abstractmethod
    async def close(self) -> None: ...


class RedisSuppressionStore(SuppressionStore):
    def __init__(self, *, url: str, namespace: str = "decisions:supp") -> None:
        import redis.asyncio as redis_async

        self._redis = redis_async.from_url(url, decode_responses=True)
        self._ns = namespace

    async def claim(self, key: str, *, ttl_s: int) -> bool:
        if ttl_s <= 0:
            return True  # rule explicitly opts out of suppression
        try:
            ok = await self._redis.set(f"{self._ns}:{key}", "1", nx=True, ex=ttl_s)
            return bool(ok)
        except Exception:  # noqa: BLE001 — fail open: better to dispatch a duplicate than lose a decision
            _FALLBACK_OPEN.inc()
            _log.warning("suppression.fallback_open", key=key)
            return True

    async def close(self) -> None:
        await self._redis.close()


class InMemorySuppressionStore(SuppressionStore):
    def __init__(self) -> None:
        self._seen: set[str] = set()

    async def claim(self, key: str, *, ttl_s: int) -> bool:
        if ttl_s <= 0:
            return True
        if key in self._seen:
            return False
        self._seen.add(key)
        return True

    async def close(self) -> None:
        return None


def record_suppressed(*, tier: str, action: str) -> None:
    _SUPPRESSED.labels(tier=tier, action=action).inc()
