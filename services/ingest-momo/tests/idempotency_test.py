from __future__ import annotations

from ingest_momo.idempotency import InMemoryIdempotencyCache


async def test_first_claim_wins() -> None:
    cache = InMemoryIdempotencyCache()
    assert await cache.claim("ev_1", ttl_s=60) is True
    assert await cache.claim("ev_1", ttl_s=60) is False
    assert await cache.claim("ev_2", ttl_s=60) is True


async def test_evicts_oldest_at_capacity() -> None:
    cache = InMemoryIdempotencyCache(max_entries=2)
    await cache.claim("ev_1", ttl_s=60)
    await cache.claim("ev_2", ttl_s=60)
    # Adding ev_3 evicts ev_1.
    await cache.claim("ev_3", ttl_s=60)
    # ev_1 should now be claimable again.
    assert await cache.claim("ev_1", ttl_s=60) is True
