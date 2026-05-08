from __future__ import annotations

import pytest

from brain_otp_guard.registry import (
    InMemoryActiveCallRegistry,
    InMemorySuppressionStore,
)


class TestInMemoryRegistry:
    @pytest.mark.asyncio
    async def test_start_then_get(self) -> None:
        r = InMemoryActiveCallRegistry()
        await r.start(callee="+233241234567", caller="+233207777777", started_at_ms=1000)
        c = await r.get("+233241234567")
        assert c is not None
        assert c.caller == "+233207777777"

    @pytest.mark.asyncio
    async def test_end_clears(self) -> None:
        r = InMemoryActiveCallRegistry()
        await r.start(callee="+233241234567", caller="+233207777777", started_at_ms=1000)
        await r.end("+233241234567")
        assert await r.get("+233241234567") is None

    @pytest.mark.asyncio
    async def test_ttl_expiry(self) -> None:
        clock = [0]

        def _clock_ms() -> int:
            return clock[0]

        r = InMemoryActiveCallRegistry(ttl_s=10, clock_ms=_clock_ms)
        await r.start(callee="+233241234567", caller="+233207777777", started_at_ms=0)
        clock[0] = 5_000
        assert await r.get("+233241234567") is not None
        clock[0] = 11_000
        assert await r.get("+233241234567") is None


class TestInMemorySuppression:
    @pytest.mark.asyncio
    async def test_first_write_passes_second_suppressed(self) -> None:
        clock = [0.0]
        s = InMemorySuppressionStore(window_s=300, clock=lambda: clock[0])
        assert await s.should_suppress("k1") is False
        assert await s.should_suppress("k1") is True

    @pytest.mark.asyncio
    async def test_window_resets(self) -> None:
        clock = [0.0]
        s = InMemorySuppressionStore(window_s=300, clock=lambda: clock[0])
        assert await s.should_suppress("k1") is False
        clock[0] = 301.0
        assert await s.should_suppress("k1") is False
