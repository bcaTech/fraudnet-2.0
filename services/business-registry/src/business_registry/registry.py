"""Business registry — Postgres-backed with Redis cache.

Hot path is `lookup_msisdn` and `lookup_shortcode` — used by every
brain-* service before scoring. The Redis layer caches positive and
negative lookups (negative TTL is shorter so newly-onboarded businesses
become visible quickly).

The Protocol-style interface lets the API and brain-* services share
one in-process implementation in dev/tests, swap for a network-RPC
implementation in production.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Iterable, Literal, Protocol
from uuid import UUID, uuid4

import asyncpg
import redis.asyncio as redis  # type: ignore[import-not-found]


@dataclass(frozen=True)
class Business:
    id: str
    name: str
    registration_number: str | None
    status: str  # 'pending' | 'verified' | 'suspended' | 'revoked'
    verified_at: str | None
    tenant_id: str = "mtn-ghana"


@dataclass(frozen=True)
class Lookup:
    """Result of an MSISDN or short-code lookup."""

    matched: bool
    business: Business | None
    is_verified: bool


# ---------------------------------------------------------------------------
# Cache layer
# ---------------------------------------------------------------------------


class _CacheClientProtocol(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ex: int | None = None) -> bool | None: ...
    async def delete(self, *keys: str) -> int: ...
    async def aclose(self) -> None: ...


class RedisCache:
    """Thin async Redis wrapper with namespace prefixes."""

    NEG_TTL_S = 60
    NS_MSISDN = "biz:msisdn:"
    NS_SHORTCODE = "biz:shortcode:"
    NEG_VALUE = "__none__"

    def __init__(
        self,
        *,
        url: str | None = None,
        client: _CacheClientProtocol | None = None,
        ttl_s: int = 300,
    ) -> None:
        if client is None:
            if url is None:
                raise ValueError("either url or client required")
            client = redis.from_url(url, decode_responses=True)
        self._client = client
        self._ttl_s = ttl_s

    async def get_msisdn(self, msisdn: str) -> Lookup | None:
        return await self._get(self.NS_MSISDN + msisdn)

    async def set_msisdn(self, msisdn: str, lookup: Lookup) -> None:
        await self._set(self.NS_MSISDN + msisdn, lookup)

    async def get_shortcode(self, code: str) -> Lookup | None:
        return await self._get(self.NS_SHORTCODE + code.upper())

    async def set_shortcode(self, code: str, lookup: Lookup) -> None:
        await self._set(self.NS_SHORTCODE + code.upper(), lookup)

    async def invalidate_msisdn(self, msisdn: str) -> None:
        await self._client.delete(self.NS_MSISDN + msisdn)

    async def invalidate_shortcode(self, code: str) -> None:
        await self._client.delete(self.NS_SHORTCODE + code.upper())

    async def _get(self, key: str) -> Lookup | None:
        v = await self._client.get(key)
        if v is None:
            return None
        if v == self.NEG_VALUE:
            return Lookup(matched=False, business=None, is_verified=False)
        try:
            data = json.loads(v)
            biz = Business(**data["business"]) if data.get("business") else None
            return Lookup(matched=bool(data["matched"]), business=biz, is_verified=bool(data["is_verified"]))
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    async def _set(self, key: str, lookup: Lookup) -> None:
        if not lookup.matched:
            await self._client.set(key, self.NEG_VALUE, ex=self.NEG_TTL_S)
            return
        payload = json.dumps(
            {
                "matched": lookup.matched,
                "is_verified": lookup.is_verified,
                "business": asdict(lookup.business) if lookup.business else None,
            }
        )
        await self._client.set(key, payload, ex=self._ttl_s)

    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        except Exception:  # noqa: BLE001
            pass


class _InMemoryCacheClient:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool | None:
        self._store[key] = value
        return True

    async def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self._store:
                del self._store[k]
                count += 1
        return count

    async def aclose(self) -> None:
        return None


def in_memory_cache(*, ttl_s: int = 300) -> RedisCache:
    return RedisCache(client=_InMemoryCacheClient(), ttl_s=ttl_s)


# ---------------------------------------------------------------------------
# Registry — Postgres operations
# ---------------------------------------------------------------------------


class Registry:
    """Postgres-backed CRUD + lookup with cache-aside.

    Constructor accepts an asyncpg.Pool. Tests pass a `pool` arg pointing
    at a sqlite or pytest-postgres fixture; in this Phase 1 build we
    expose a fully unit-testable lookup path via `InMemoryRegistry`
    below for the brain-* integration tests.
    """

    def __init__(self, *, pool: asyncpg.Pool, cache: RedisCache | None = None) -> None:
        self._pool = pool
        self._cache = cache

    async def create_business(
        self,
        *,
        name: str,
        registration_number: str | None = None,
        tenant_id: str = "mtn-ghana",
    ) -> Business:
        bid = uuid4()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO businesses (id, name, registration_number, status, tenant_id)
                VALUES ($1, $2, $3, 'pending', $4)
                RETURNING id, name, registration_number, status, verified_at, tenant_id
                """,
                bid,
                name,
                registration_number,
                tenant_id,
            )
            assert row is not None
            return _row_to_business(row)

    async def verify_business(self, *, business_id: str, verified_by: str | None = None) -> Business:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE businesses
                   SET status = 'verified',
                       verified_at = now(),
                       verified_by = $2,
                       updated_at = now()
                 WHERE id = $1
                 RETURNING id, name, registration_number, status, verified_at, tenant_id
                """,
                UUID(business_id),
                UUID(verified_by) if verified_by else None,
            )
        if row is None:
            raise LookupError(f"business {business_id} not found")
        # Cache invalidation cascades — purge all linked lookups.
        await self._purge_business_cache(business_id)
        return _row_to_business(row)

    async def add_msisdn(
        self,
        *,
        business_id: str,
        msisdn: str,
        kind: Literal["voice", "sms", "both"] = "both",
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO business_msisdns (business_id, msisdn, kind, verified_at)
                VALUES ($1, $2, $3, now())
                ON CONFLICT (business_id, msisdn) DO UPDATE
                  SET kind = EXCLUDED.kind, verified_at = now()
                """,
                UUID(business_id),
                msisdn,
                kind,
            )
        if self._cache is not None:
            await self._cache.invalidate_msisdn(msisdn)

    async def add_shortcode(self, *, business_id: str, shortcode: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO business_shortcodes (business_id, shortcode, verified_at)
                VALUES ($1, $2, now())
                ON CONFLICT (business_id, shortcode) DO UPDATE
                  SET verified_at = now()
                """,
                UUID(business_id),
                shortcode.upper(),
            )
        if self._cache is not None:
            await self._cache.invalidate_shortcode(shortcode)

    async def get_business(self, business_id: str) -> Business | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, name, registration_number, status, verified_at, tenant_id
                  FROM businesses
                 WHERE id = $1
                """,
                UUID(business_id),
            )
        return _row_to_business(row) if row else None

    async def lookup_msisdn(self, msisdn: str) -> Lookup:
        if self._cache is not None:
            cached = await self._cache.get_msisdn(msisdn)
            if cached is not None:
                return cached
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT b.id, b.name, b.registration_number, b.status, b.verified_at, b.tenant_id
                  FROM business_msisdns m
                  JOIN businesses b ON b.id = m.business_id
                 WHERE m.msisdn = $1
                """,
                msisdn,
            )
        result = self._row_to_lookup(row)
        if self._cache is not None:
            await self._cache.set_msisdn(msisdn, result)
        return result

    async def lookup_shortcode(self, shortcode: str) -> Lookup:
        code = shortcode.upper()
        if self._cache is not None:
            cached = await self._cache.get_shortcode(code)
            if cached is not None:
                return cached
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT b.id, b.name, b.registration_number, b.status, b.verified_at, b.tenant_id
                  FROM business_shortcodes s
                  JOIN businesses b ON b.id = s.business_id
                 WHERE s.shortcode = $1
                """,
                code,
            )
        result = self._row_to_lookup(row)
        if self._cache is not None:
            await self._cache.set_shortcode(code, result)
        return result

    async def list_businesses(self, status: str | None = None) -> list[Business]:
        async with self._pool.acquire() as conn:
            if status:
                rows = await conn.fetch(
                    """
                    SELECT id, name, registration_number, status, verified_at, tenant_id
                      FROM businesses
                     WHERE status = $1
                  ORDER BY name
                    """,
                    status,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT id, name, registration_number, status, verified_at, tenant_id
                      FROM businesses
                  ORDER BY name
                    """
                )
        return [_row_to_business(r) for r in rows]

    @staticmethod
    def _row_to_lookup(row) -> Lookup:
        if row is None:
            return Lookup(matched=False, business=None, is_verified=False)
        biz = _row_to_business(row)
        return Lookup(matched=True, business=biz, is_verified=biz.status == "verified")

    async def _purge_business_cache(self, business_id: str) -> None:
        if self._cache is None:
            return
        async with self._pool.acquire() as conn:
            ms = await conn.fetch(
                "SELECT msisdn FROM business_msisdns WHERE business_id = $1",
                UUID(business_id),
            )
            cs = await conn.fetch(
                "SELECT shortcode FROM business_shortcodes WHERE business_id = $1",
                UUID(business_id),
            )
        for r in ms:
            await self._cache.invalidate_msisdn(r["msisdn"])
        for r in cs:
            await self._cache.invalidate_shortcode(r["shortcode"])


def _row_to_business(row) -> Business:
    return Business(
        id=str(row["id"]),
        name=row["name"],
        registration_number=row.get("registration_number"),
        status=row["status"],
        verified_at=row["verified_at"].isoformat() if row.get("verified_at") else None,
        tenant_id=row.get("tenant_id", "mtn-ghana"),
    )


# ---------------------------------------------------------------------------
# In-memory implementation — for unit tests and brain-* integration tests
# ---------------------------------------------------------------------------


class InMemoryRegistry:
    """Pure-Python registry for tests. No DB, no Redis."""

    def __init__(self) -> None:
        self._businesses: dict[str, Business] = {}
        self._msisdns: dict[str, str] = {}  # msisdn → business_id
        self._shortcodes: dict[str, str] = {}  # SHORTCODE → business_id

    async def create_business(
        self,
        *,
        name: str,
        registration_number: str | None = None,
        tenant_id: str = "mtn-ghana",
    ) -> Business:
        bid = str(uuid4())
        biz = Business(
            id=bid,
            name=name,
            registration_number=registration_number,
            status="pending",
            verified_at=None,
            tenant_id=tenant_id,
        )
        self._businesses[bid] = biz
        return biz

    async def verify_business(self, *, business_id: str, verified_by: str | None = None) -> Business:
        biz = self._businesses.get(business_id)
        if biz is None:
            raise LookupError(business_id)
        from datetime import UTC, datetime

        new_biz = Business(
            id=biz.id,
            name=biz.name,
            registration_number=biz.registration_number,
            status="verified",
            verified_at=datetime.now(UTC).isoformat(),
            tenant_id=biz.tenant_id,
        )
        self._businesses[business_id] = new_biz
        return new_biz

    async def add_msisdn(
        self, *, business_id: str, msisdn: str, kind: str = "both"
    ) -> None:
        if business_id not in self._businesses:
            raise LookupError(business_id)
        self._msisdns[msisdn] = business_id

    async def add_shortcode(self, *, business_id: str, shortcode: str) -> None:
        if business_id not in self._businesses:
            raise LookupError(business_id)
        self._shortcodes[shortcode.upper()] = business_id

    async def get_business(self, business_id: str) -> Business | None:
        return self._businesses.get(business_id)

    async def lookup_msisdn(self, msisdn: str) -> Lookup:
        bid = self._msisdns.get(msisdn)
        if bid is None:
            return Lookup(matched=False, business=None, is_verified=False)
        biz = self._businesses[bid]
        return Lookup(matched=True, business=biz, is_verified=biz.status == "verified")

    async def lookup_shortcode(self, shortcode: str) -> Lookup:
        bid = self._shortcodes.get(shortcode.upper())
        if bid is None:
            return Lookup(matched=False, business=None, is_verified=False)
        biz = self._businesses[bid]
        return Lookup(matched=True, business=biz, is_verified=biz.status == "verified")

    async def list_businesses(self, status: str | None = None) -> list[Business]:
        out = list(self._businesses.values())
        if status is not None:
            out = [b for b in out if b.status == status]
        return sorted(out, key=lambda b: b.name)
