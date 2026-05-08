"""Per-analyst rate limit (LLM cost control).

Token-bucket; default 10/hour (i.e. capacity=10, refill=10/3600 per s).
GROUP_ADMIN bypasses for incident triage; the bypass is documented and
audit-logged at the route layer.

Falls back to in-memory when Redis is unavailable so dev / CI do not
require it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from fraudnet.obs import counter, get_logger

_log = get_logger("brain_agent.rate_limit")

_RATE_HITS = counter(
    "brain_agent_rate_limit_hits_total",
    "Investigations denied by the per-analyst rate limiter.",
    labelnames=("analyst_id",),
)


@dataclass(frozen=True)
class RateLimitConfig:
    capacity: int = 10
    refill_per_s: float = 10 / 3600.0


class RateLimiter(Protocol):
    async def allow(self, analyst_id: str, *, cost: int = 1) -> bool: ...


class InMemoryRateLimiter:
    def __init__(self, *, config: RateLimitConfig) -> None:
        self._config = config
        self._buckets: dict[str, tuple[float, float]] = {}

    async def allow(self, analyst_id: str, *, cost: int = 1) -> bool:
        now = time.time()
        tokens, ts = self._buckets.get(
            analyst_id, (float(self._config.capacity), now)
        )
        elapsed = max(0.0, now - ts)
        tokens = min(
            float(self._config.capacity),
            tokens + elapsed * self._config.refill_per_s,
        )
        if tokens < cost:
            self._buckets[analyst_id] = (tokens, now)
            _RATE_HITS.labels(analyst_id=analyst_id).inc()
            return False
        self._buckets[analyst_id] = (tokens - cost, now)
        return True


_LUA_SCRIPT = """
local bucket = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill = tonumber(ARGV[2])
local now_ms = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
local data = redis.call('HMGET', bucket, 'tokens', 'ts_ms')
local tokens = tonumber(data[1])
local ts_ms = tonumber(data[2])
if tokens == nil then
  tokens = capacity
  ts_ms = now_ms
else
  local elapsed_s = math.max(0, (now_ms - ts_ms) / 1000.0)
  tokens = math.min(capacity, tokens + elapsed_s * refill)
  ts_ms = now_ms
end
local allowed = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
end
redis.call('HMSET', bucket, 'tokens', tokens, 'ts_ms', ts_ms)
local ttl = math.ceil((capacity / math.max(refill, 0.0001)) * 2)
redis.call('EXPIRE', bucket, ttl)
return allowed
"""


class RedisRateLimiter:
    def __init__(self, *, redis: object, config: RateLimitConfig) -> None:
        self._redis = redis
        self._config = config
        self._sha: str | None = None

    async def _ensure(self) -> str:
        if self._sha is not None:
            return self._sha
        sha = await self._redis.script_load(_LUA_SCRIPT)  # type: ignore[attr-defined]
        self._sha = sha if isinstance(sha, str) else sha.decode()
        return self._sha

    async def allow(self, analyst_id: str, *, cost: int = 1) -> bool:
        try:
            sha = await self._ensure()
            result = await self._redis.evalsha(  # type: ignore[attr-defined]
                sha,
                1,
                f"brain_agent:rl:{analyst_id}",
                self._config.capacity,
                self._config.refill_per_s,
                int(time.time() * 1000),
                cost,
            )
            allowed = int(result) == 1
        except Exception:  # noqa: BLE001
            _log.warning("brain_agent.rate_limit.redis_unavailable", analyst=analyst_id)
            return True
        if not allowed:
            _RATE_HITS.labels(analyst_id=analyst_id).inc()
        return allowed
