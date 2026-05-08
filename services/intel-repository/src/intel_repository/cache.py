"""Redis hot-lookup cache.

Wraps the repo for sub-millisecond lookups during the scoring path.
Cache key shape: `intel:{tenant}:{kind}:{identifier}` → "1|{score}"
on hit, "0" on miss. Falls back to the repo on cache miss / Redis
failure.

Misses are positively cached (with a shorter TTL than hits) so a busy
"is this MSISDN suspect?" query path doesn't hammer Postgres on every
not-flagged number.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from fraudnet.obs import counter, get_logger

from intel_repository.repo import HOT_KINDS, IntelRepo

_log = get_logger("intel_repository.cache")
_CACHE = counter(
    "intel_repository_cache_total",
    "Intel repository cache outcomes.",
    labelnames=("kind", "outcome"),
)


@dataclass(frozen=True)
class IntelHit:
    hit: bool
    score: float = 0.0
    metadata: dict | None = None
    last_seen_at: float | None = None
    cache_hit: bool = False


class CachedIntelRepo:
    def __init__(self, *, repo: IntelRepo, redis: object | None, hit_ttl_s: int = 300) -> None:
        self._repo = repo
        self._redis = redis
        self._hit_ttl_s = hit_ttl_s
        self._miss_ttl_s = max(30, hit_ttl_s // 5)

    async def lookup(
        self,
        *,
        kind: str,
        identifier: str,
        tenant_id: str = "mtn-ghana",
    ) -> IntelHit:
        if kind in HOT_KINDS and self._redis is not None:
            cache_key = f"intel:{tenant_id}:{kind}:{identifier}"
            try:
                raw = await self._redis.get(cache_key)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                raw = None
            if raw is not None:
                outcome = "cache_hit_present" if raw and not raw.startswith(b"0") else "cache_hit_absent"
                _CACHE.labels(kind=kind, outcome=outcome).inc()
                if raw.startswith(b"0"):
                    return IntelHit(hit=False, cache_hit=True)
                _, _, score_str = raw.partition(b"|")
                try:
                    score = float(score_str)
                except ValueError:
                    score = 0.0
                return IntelHit(hit=True, score=score, cache_hit=True)
            _CACHE.labels(kind=kind, outcome="cache_miss").inc()

        row = await self._repo.get(kind=kind, identifier=identifier, tenant_id=tenant_id)
        hit = row is not None
        if kind in HOT_KINDS and self._redis is not None:
            try:
                value = (
                    f"1|{row['risk_score']}".encode()
                    if hit
                    else b"0"
                )
                ttl = self._hit_ttl_s if hit else self._miss_ttl_s
                await self._redis.setex(  # type: ignore[attr-defined]
                    f"intel:{tenant_id}:{kind}:{identifier}",
                    ttl,
                    value,
                )
            except Exception:  # noqa: BLE001
                pass

        if not hit:
            return IntelHit(hit=False)
        return IntelHit(
            hit=True,
            score=float(row["risk_score"]),
            metadata=row.get("metadata"),
            last_seen_at=(
                row["last_seen_at"].timestamp()
                if row.get("last_seen_at") is not None
                else None
            ),
        )

    async def invalidate(self, *, kind: str, identifier: str, tenant_id: str = "mtn-ghana") -> None:
        if self._redis is None:
            return
        try:
            await self._redis.delete(f"intel:{tenant_id}:{kind}:{identifier}")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


# Suppress unused
_ = time
