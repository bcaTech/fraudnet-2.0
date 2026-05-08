from __future__ import annotations

from api_customer.otp import InMemoryOtpAdapter


async def test_in_memory_otp_happy_path() -> None:
    a = InMemoryOtpAdapter()
    status = await a.request("+233241234567")
    assert status.delivered is True
    assert await a.verify("+233241234567", "123456") is True


async def test_in_memory_otp_wrong_code() -> None:
    a = InMemoryOtpAdapter()
    await a.request("+233241234567")
    assert await a.verify("+233241234567", "000000") is False


async def test_in_memory_otp_unrequested() -> None:
    a = InMemoryOtpAdapter()
    assert await a.verify("+233241234567", "123456") is False


async def test_in_memory_otp_one_shot() -> None:
    a = InMemoryOtpAdapter()
    await a.request("+233241234567")
    assert await a.verify("+233241234567", "123456") is True
    # Already consumed
    assert await a.verify("+233241234567", "123456") is False
