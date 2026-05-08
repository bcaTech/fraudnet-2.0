from __future__ import annotations

from decisions.suppression import InMemorySuppressionStore


async def test_first_claim_wins() -> None:
    s = InMemorySuppressionStore()
    assert await s.claim("k1", ttl_s=60) is True
    assert await s.claim("k1", ttl_s=60) is False
    assert await s.claim("k2", ttl_s=60) is True


async def test_zero_ttl_disables_suppression() -> None:
    s = InMemorySuppressionStore()
    assert await s.claim("k", ttl_s=0) is True
    assert await s.claim("k", ttl_s=0) is True
