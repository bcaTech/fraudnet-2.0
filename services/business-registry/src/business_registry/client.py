"""HTTP client for business-registry.

Used by brain-behavioural and brain-content to look up MSISDNs / short
codes before scoring. Must be cheap (Redis-cached on the server side; the
client adds a per-process cache too).

The Protocol form lets brain-* services swap to the in-process registry
in tests without going through HTTP.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass(frozen=True)
class ClientLookup:
    matched: bool
    is_verified: bool
    business_id: str | None
    business_name: str | None


class BusinessRegistryClient(Protocol):
    async def lookup_msisdn(self, msisdn: str) -> ClientLookup: ...
    async def lookup_shortcode(self, shortcode: str) -> ClientLookup: ...
    async def aclose(self) -> None: ...


class HttpBusinessRegistryClient:
    """httpx-based client with a small in-process LRU cache."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_s: float = 0.05,
        cache_size: int = 1024,
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_s)
        self._cache: dict[str, ClientLookup] = {}
        self._cache_size = cache_size
        self._lock = asyncio.Lock()

    async def lookup_msisdn(self, msisdn: str) -> ClientLookup:
        return await self._lookup(f"/lookup/msisdn/{msisdn}", "msisdn:" + msisdn)

    async def lookup_shortcode(self, shortcode: str) -> ClientLookup:
        return await self._lookup(
            f"/lookup/shortcode/{shortcode.upper()}", "shortcode:" + shortcode.upper()
        )

    async def _lookup(self, path: str, key: str) -> ClientLookup:
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            r = await self._client.get(path)
        except (httpx.HTTPError, httpx.TimeoutException):
            return ClientLookup(matched=False, is_verified=False, business_id=None, business_name=None)
        if r.status_code != 200:
            return ClientLookup(matched=False, is_verified=False, business_id=None, business_name=None)
        body = r.json()
        biz = body.get("business")
        result = ClientLookup(
            matched=bool(body.get("matched")),
            is_verified=bool(body.get("is_verified")),
            business_id=biz["id"] if biz else None,
            business_name=biz["name"] if biz else None,
        )
        async with self._lock:
            if len(self._cache) >= self._cache_size:
                # Cheap eviction — drop one arbitrary entry.
                self._cache.pop(next(iter(self._cache), ""), None)
            self._cache[key] = result
        return result

    async def aclose(self) -> None:
        await self._client.aclose()


class NoopBusinessRegistryClient:
    """Default for environments where business-registry is not deployed."""

    async def lookup_msisdn(self, msisdn: str) -> ClientLookup:
        return ClientLookup(matched=False, is_verified=False, business_id=None, business_name=None)

    async def lookup_shortcode(self, shortcode: str) -> ClientLookup:
        return ClientLookup(matched=False, is_verified=False, business_id=None, business_name=None)

    async def aclose(self) -> None:
        return None
