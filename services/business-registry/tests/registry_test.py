from __future__ import annotations

import pytest

from business_registry.registry import InMemoryRegistry, in_memory_cache


class TestInMemoryRegistry:
    @pytest.mark.asyncio
    async def test_create_and_lookup_unverified(self) -> None:
        r = InMemoryRegistry()
        biz = await r.create_business(name="Acme Bank", registration_number="GH-12345")
        await r.add_msisdn(business_id=biz.id, msisdn="+233231000000")
        lookup = await r.lookup_msisdn("+233231000000")
        assert lookup.matched is True
        assert lookup.is_verified is False
        assert lookup.business is not None
        assert lookup.business.name == "Acme Bank"

    @pytest.mark.asyncio
    async def test_verify_then_lookup(self) -> None:
        r = InMemoryRegistry()
        biz = await r.create_business(name="Ecobank")
        await r.add_msisdn(business_id=biz.id, msisdn="+233231100000")
        await r.verify_business(business_id=biz.id)
        lookup = await r.lookup_msisdn("+233231100000")
        assert lookup.is_verified is True

    @pytest.mark.asyncio
    async def test_shortcode_lookup_uppercases(self) -> None:
        r = InMemoryRegistry()
        biz = await r.create_business(name="MTN GHANA")
        await r.add_shortcode(business_id=biz.id, shortcode="mtn")
        await r.verify_business(business_id=biz.id)
        lookup = await r.lookup_shortcode("MTN")
        assert lookup.is_verified is True
        lookup2 = await r.lookup_shortcode("mtn")
        assert lookup2.is_verified is True

    @pytest.mark.asyncio
    async def test_unknown_msisdn_lookup(self) -> None:
        r = InMemoryRegistry()
        lookup = await r.lookup_msisdn("+233244444444")
        assert lookup.matched is False
        assert lookup.is_verified is False

    @pytest.mark.asyncio
    async def test_list_filters_by_status(self) -> None:
        r = InMemoryRegistry()
        a = await r.create_business(name="A Co")
        await r.create_business(name="B Co")
        await r.verify_business(business_id=a.id)
        verified = await r.list_businesses(status="verified")
        pending = await r.list_businesses(status="pending")
        assert len(verified) == 1 and verified[0].name == "A Co"
        assert len(pending) == 1 and pending[0].name == "B Co"


class TestRedisCacheInMemory:
    @pytest.mark.asyncio
    async def test_negative_caching(self) -> None:
        from business_registry.registry import Lookup

        cache = in_memory_cache(ttl_s=300)
        await cache.set_msisdn("+233244000000", Lookup(matched=False, business=None, is_verified=False))
        cached = await cache.get_msisdn("+233244000000")
        assert cached is not None
        assert cached.matched is False

    @pytest.mark.asyncio
    async def test_positive_caching_roundtrips_business(self) -> None:
        from business_registry.registry import Business, Lookup

        cache = in_memory_cache(ttl_s=300)
        biz = Business(
            id="00000000-0000-0000-0000-000000000001",
            name="Test Co",
            registration_number=None,
            status="verified",
            verified_at="2026-05-01T00:00:00+00:00",
        )
        await cache.set_msisdn(
            "+233244000001",
            Lookup(matched=True, business=biz, is_verified=True),
        )
        cached = await cache.get_msisdn("+233244000001")
        assert cached is not None
        assert cached.matched is True
        assert cached.business is not None
        assert cached.business.name == "Test Co"
