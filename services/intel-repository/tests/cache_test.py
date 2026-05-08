"""Cache: positive + negative caching, fallback when Redis is absent."""

from __future__ import annotations

from typing import Any

import pytest

from intel_repository.cache import CachedIntelRepo


class _FakeRepo:
    def __init__(self, present: dict[str, dict[str, Any]] | None = None) -> None:
        self._present = present or {}
        self.calls: list[tuple[str, str, str]] = []

    async def get(self, *, kind: str, identifier: str, tenant_id: str = "mtn-ghana"):  # noqa: ANN201
        self.calls.append((kind, identifier, tenant_id))
        return self._present.get(f"{kind}:{identifier}")


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.gets = 0
        self.sets = 0

    async def get(self, key: str) -> bytes | None:
        self.gets += 1
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: bytes) -> None:
        self.sets += 1
        self.store[key] = value

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


async def test_lookup_negative_cache_hit_skips_repo() -> None:
    """Second call for a known-absent identifier should not hit the repo."""
    repo = _FakeRepo()
    redis = _FakeRedis()
    cache = CachedIntelRepo(repo=repo, redis=redis)  # type: ignore[arg-type]

    r1 = await cache.lookup(kind="suspect_number", identifier="+233200000001")
    assert r1.hit is False
    assert len(repo.calls) == 1

    r2 = await cache.lookup(kind="suspect_number", identifier="+233200000001")
    assert r2.hit is False
    assert r2.cache_hit is True
    assert len(repo.calls) == 1  # repo not called again


async def test_lookup_positive_cache_hit_returns_score() -> None:
    repo = _FakeRepo(
        present={
            "suspect_number:+233200000001": {
                "risk_score": 0.92,
                "metadata": {"signal_kind": "voice.velocity_burst"},
                "last_seen_at": None,
            }
        }
    )
    redis = _FakeRedis()
    cache = CachedIntelRepo(repo=repo, redis=redis)  # type: ignore[arg-type]

    r1 = await cache.lookup(kind="suspect_number", identifier="+233200000001")
    assert r1.hit is True
    assert r1.score == pytest.approx(0.92)

    r2 = await cache.lookup(kind="suspect_number", identifier="+233200000001")
    assert r2.cache_hit is True
    assert r2.hit is True
    assert r2.score == pytest.approx(0.92)


async def test_lookup_unknown_kind_skips_cache() -> None:
    """Cold kinds bypass Redis even when present."""
    repo = _FakeRepo()
    redis = _FakeRedis()
    cache = CachedIntelRepo(repo=repo, redis=redis)  # type: ignore[arg-type]

    await cache.lookup(kind="unallocated_range", identifier="2330099")
    assert redis.gets == 0
    assert redis.sets == 0


async def test_lookup_no_redis_falls_back_to_repo() -> None:
    repo = _FakeRepo()
    cache = CachedIntelRepo(repo=repo, redis=None)
    r = await cache.lookup(kind="suspect_number", identifier="+233200000001")
    assert r.hit is False
    assert r.cache_hit is False
    assert len(repo.calls) == 1


async def test_invalidate_drops_cached_value() -> None:
    repo = _FakeRepo()
    redis = _FakeRedis()
    cache = CachedIntelRepo(repo=repo, redis=redis)  # type: ignore[arg-type]

    # Prime the negative cache.
    await cache.lookup(kind="suspect_number", identifier="x")
    assert any(k.endswith(":suspect_number:x") for k in redis.store)

    await cache.invalidate(kind="suspect_number", identifier="x")
    assert not any(k.endswith(":suspect_number:x") for k in redis.store)
