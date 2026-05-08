"""Active-call registry — Redis-backed, multi-instance safe.

Tracks which MSISDNs currently have an active *inbound* call. The runner
calls `start(callee, caller, ts)` on CALL_START and `end(callee)` on
CALL_END. Registry entries auto-expire after `ttl_s` to protect against
missed CALL_END events (probe flaps, vendor outages).

The implementation is split into a Protocol and a Redis-backed default
so tests can inject an in-memory fake.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

import redis.asyncio as redis  # type: ignore[import-not-found]


@dataclass(frozen=True)
class ActiveCall:
    callee: str  # MSISDN
    caller: str  # MSISDN
    started_at_ms: int


class ActiveCallRegistry(Protocol):
    async def start(self, *, callee: str, caller: str, started_at_ms: int) -> None: ...
    async def end(self, callee: str) -> None: ...
    async def get(self, callee: str) -> ActiveCall | None: ...
    async def aclose(self) -> None: ...


class RedisActiveCallRegistry:
    """Redis-backed registry. One key per MSISDN with TTL.

    Key shape: `otp:active:<msisdn>` → hash {caller, started_at_ms}.
    """

    def __init__(self, *, url: str, ttl_s: int = 900, client: redis.Redis | None = None) -> None:
        self._client = client or redis.from_url(url, decode_responses=True)
        self._ttl_s = ttl_s

    @staticmethod
    def _key(msisdn: str) -> str:
        return f"otp:active:{msisdn}"

    async def start(self, *, callee: str, caller: str, started_at_ms: int) -> None:
        key = self._key(callee)
        # HSET + EXPIRE in a pipeline; lazy-creates the hash if absent.
        pipe = self._client.pipeline()
        pipe.hset(key, mapping={"caller": caller, "started_at_ms": str(started_at_ms)})
        pipe.expire(key, self._ttl_s)
        await pipe.execute()

    async def end(self, callee: str) -> None:
        await self._client.delete(self._key(callee))

    async def get(self, callee: str) -> ActiveCall | None:
        data = await self._client.hgetall(self._key(callee))
        if not data:
            return None
        try:
            return ActiveCall(
                callee=callee,
                caller=str(data.get("caller", "")),
                started_at_ms=int(data.get("started_at_ms", "0")),
            )
        except (ValueError, TypeError):
            return None

    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        except Exception:  # noqa: BLE001 — best effort
            pass


class SuppressionStore(Protocol):
    async def should_suppress(self, key: str) -> bool: ...
    async def aclose(self) -> None: ...


class RedisSuppressionStore:
    """Per-key suppression with TTL. SETNX-style — first write wins."""

    def __init__(
        self, *, url: str, window_s: int = 300, client: redis.Redis | None = None
    ) -> None:
        self._client = client or redis.from_url(url, decode_responses=True)
        self._window_s = window_s

    async def should_suppress(self, key: str) -> bool:
        # SET key value NX EX window — returns True if set (no suppression),
        # None/False if already present (suppress).
        ok = await self._client.set(
            f"otp:suppress:{key}", str(int(time.time())), nx=True, ex=self._window_s
        )
        return ok is None or ok is False

    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        except Exception:  # noqa: BLE001
            pass


# In-memory implementations — used by tests and the in-process dev mode.


class InMemoryActiveCallRegistry:
    def __init__(self, *, ttl_s: int = 900, clock_ms=lambda: int(time.time() * 1000)) -> None:
        self._ttl_ms = ttl_s * 1000
        self._calls: dict[str, ActiveCall] = {}
        self._clock_ms = clock_ms

    async def start(self, *, callee: str, caller: str, started_at_ms: int) -> None:
        self._calls[callee] = ActiveCall(callee=callee, caller=caller, started_at_ms=started_at_ms)

    async def end(self, callee: str) -> None:
        self._calls.pop(callee, None)

    async def get(self, callee: str) -> ActiveCall | None:
        c = self._calls.get(callee)
        if c is None:
            return None
        if self._clock_ms() - c.started_at_ms > self._ttl_ms:
            self._calls.pop(callee, None)
            return None
        return c

    async def aclose(self) -> None:
        return None


class InMemorySuppressionStore:
    def __init__(
        self, *, window_s: int = 300, clock=lambda: time.time()
    ) -> None:
        self._window_s = window_s
        self._seen: dict[str, float] = {}
        self._clock = clock

    async def should_suppress(self, key: str) -> bool:
        now = self._clock()
        last = self._seen.get(key)
        if last is not None and now - last < self._window_s:
            return True
        self._seen[key] = now
        return False

    async def aclose(self) -> None:
        return None
