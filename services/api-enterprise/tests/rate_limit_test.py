"""In-memory rate limiter — drains, refills, multi-tenant isolation."""

from __future__ import annotations

import asyncio

import pytest

from api_enterprise.rate_limit import InMemoryRateLimiter, RateLimitConfig


async def test_in_memory_drains_then_denies() -> None:
    limiter = InMemoryRateLimiter(config=RateLimitConfig(capacity=3, refill_per_s=0))
    assert await limiter.allow("acme") is True
    assert await limiter.allow("acme") is True
    assert await limiter.allow("acme") is True
    assert await limiter.allow("acme") is False


async def test_in_memory_refills() -> None:
    limiter = InMemoryRateLimiter(
        config=RateLimitConfig(capacity=2, refill_per_s=100.0)
    )
    assert await limiter.allow("acme") is True
    assert await limiter.allow("acme") is True
    assert await limiter.allow("acme") is False
    await asyncio.sleep(0.05)  # ~5 tokens refilled at 100/s
    assert await limiter.allow("acme") is True


async def test_in_memory_isolates_tenants() -> None:
    """Draining tenant A's bucket must not affect tenant B."""
    limiter = InMemoryRateLimiter(config=RateLimitConfig(capacity=2, refill_per_s=0))
    assert await limiter.allow("acme") is True
    assert await limiter.allow("acme") is True
    assert await limiter.allow("acme") is False
    # Tenant B should still have its full bucket.
    assert await limiter.allow("globex") is True
    assert await limiter.allow("globex") is True
    assert await limiter.allow("globex") is False


@pytest.mark.parametrize(
    ("capacity", "refill", "burst", "expect_allow_after_burst"),
    [
        (5, 0, 5, False),     # exhausted
        (5, 0, 4, True),      # one left
        (10, 1000, 10, True), # high refill — instant
    ],
)
async def test_in_memory_table(
    capacity: int, refill: float, burst: int, expect_allow_after_burst: bool
) -> None:
    limiter = InMemoryRateLimiter(
        config=RateLimitConfig(capacity=capacity, refill_per_s=refill)
    )
    for _ in range(burst):
        assert await limiter.allow("t") is True
    if expect_allow_after_burst:
        await asyncio.sleep(0.02)
    assert await limiter.allow("t") is expect_allow_after_burst
