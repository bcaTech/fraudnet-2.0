"""Per-tenant token-bucket rate limiter, Redis-backed.

The bucket is implemented as a single Redis Lua script so consume + refill
are atomic. State lives in `enterprise:rl:{tenant_id}` with two fields,
`tokens` (float, current balance) and `ts_ms` (last refill).

Falls back to an in-memory limiter when Redis is unavailable so dev / CI do
not require the Redis container. Production always points at Redis; the
fallback is not a substitute for it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis  # type: ignore[import-untyped]

from fraudnet.obs import counter, get_logger

_log = get_logger("api_enterprise.rate_limit")

_RATE_LIMIT_HITS = counter(
    "api_enterprise_rate_limit_hits_total",
    "Requests denied by the per-tenant rate limiter.",
    labelnames=("tenant_id",),
)


# Atomic consume-or-deny. KEYS[1] = bucket key, ARGV = capacity, refill_per_s,
# now_ms, cost. Returns 1 (allowed) or 0 (denied).
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
-- Expire after 2x the time it would take to fully refill, to bound footprint.
local ttl = math.ceil((capacity / math.max(refill, 0.001)) * 2)
redis.call('EXPIRE', bucket, ttl)
return allowed
"""


@dataclass(frozen=True)
class RateLimitConfig:
    capacity: int = 60
    refill_per_s: float = 10.0


class RateLimiter(Protocol):
    async def allow(self, tenant_id: str, *, cost: int = 1) -> bool: ...


class RedisRateLimiter:
    def __init__(self, *, redis: Redis, config: RateLimitConfig) -> None:
        self._redis = redis
        self._config = config
        self._script_sha: str | None = None

    async def _ensure_script(self) -> str:
        if self._script_sha is not None:
            return self._script_sha
        sha = await self._redis.script_load(_LUA_SCRIPT)
        self._script_sha = sha if isinstance(sha, str) else sha.decode()
        return self._script_sha

    async def allow(self, tenant_id: str, *, cost: int = 1) -> bool:
        key = f"enterprise:rl:{tenant_id}"
        now_ms = int(time.time() * 1000)
        try:
            sha = await self._ensure_script()
            result = await self._redis.evalsha(
                sha,
                1,
                key,
                self._config.capacity,
                self._config.refill_per_s,
                now_ms,
                cost,
            )
            allowed = int(result) == 1
        except Exception:  # noqa: BLE001
            # Redis failure must fail-open for the request, but log it loud.
            # The alternative — fail-closed — degrades a global outage of one
            # tenant into a global outage of all tenants. Open is the safer
            # default for a B2B API.
            _log.warning("api_enterprise.rate_limit.redis_unavailable", tenant=tenant_id)
            return True
        if not allowed:
            _RATE_LIMIT_HITS.labels(tenant_id=tenant_id).inc()
        return allowed


class InMemoryRateLimiter:
    """Single-process token bucket. Test / dev only."""

    def __init__(self, *, config: RateLimitConfig) -> None:
        self._config = config
        self._buckets: dict[str, tuple[float, float]] = {}

    async def allow(self, tenant_id: str, *, cost: int = 1) -> bool:
        now = time.time()
        tokens, ts = self._buckets.get(tenant_id, (float(self._config.capacity), now))
        elapsed = max(0.0, now - ts)
        tokens = min(float(self._config.capacity), tokens + elapsed * self._config.refill_per_s)
        if tokens < cost:
            self._buckets[tenant_id] = (tokens, now)
            _RATE_LIMIT_HITS.labels(tenant_id=tenant_id).inc()
            return False
        self._buckets[tenant_id] = (tokens - cost, now)
        return True
