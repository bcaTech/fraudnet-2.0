"""URL blocklist — Redis-backed.

Storage:
  - `urlintel:domains` — Redis set of normalised bare domains (no scheme,
    no path). The DNS sinkhole pulls the full set via /blocklist/export.
  - `urlintel:meta:<domain>` — hash of source, category, confidence,
    added_at_ms, ttl. Used by /blocklist/check to return rich verdicts.

Allow-list:
  An in-memory frozenset checked before any add and on every check —
  *the* belt-and-braces guard against blocking critical services.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Iterable, Protocol

import redis.asyncio as redis  # type: ignore[import-not-found]

# Strict bare-domain regex (lower-cased, dotted, no path/scheme).
_HOST_RE = re.compile(r"^(?:[a-z]+://)?([^/?#]+)", re.IGNORECASE)
_DOMAIN_RE = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$")


def normalise_domain(value: str) -> str:
    """Extract a bare lower-cased domain from a URL or domain-like string.

    Strips scheme, path, query, fragment and port. Returns the input
    lower-cased on no-match — caller decides whether to accept.
    """
    if not value:
        return ""
    s = value.strip().lower()
    m = _HOST_RE.match(s)
    host = m.group(1) if m else s
    # Strip port if any.
    host = host.split(":", 1)[0]
    return host


@dataclass(frozen=True)
class BlocklistEntry:
    domain: str
    source: str  # 'manual' | 'feed:<name>' | 'signals' | etc.
    category: str  # 'phishing' | 'malware' | 'scam' | 'smishing' | 'unknown'
    confidence: float  # 0..1
    added_at_ms: int


@dataclass(frozen=True)
class CheckResult:
    blocked: bool
    domain: str
    matched: str | None  # the domain or parent domain matched
    entry: BlocklistEntry | None
    allow_listed: bool


class _ClientProtocol(Protocol):
    async def sadd(self, name: str, *values: str) -> int: ...
    async def srem(self, name: str, *values: str) -> int: ...
    async def sismember(self, name: str, value: str) -> bool: ...
    async def smembers(self, name: str) -> set[str]: ...
    async def hset(self, name: str, mapping: dict[str, str]) -> int: ...
    async def hgetall(self, name: str) -> dict[str, str]: ...
    async def delete(self, *names: str) -> int: ...
    async def expire(self, name: str, time: int) -> bool: ...
    async def aclose(self) -> None: ...


class Blocklist:
    """Redis-backed blocklist with allow-list filtering.

    Construction parameters separate runtime client (`client`) from URL —
    tests pass an injected client (e.g. `fakeredis`) directly.
    """

    SET_KEY = "urlintel:domains"

    def __init__(
        self,
        *,
        url: str | None = None,
        client: _ClientProtocol | None = None,
        allow_list: Iterable[str] = (),
        signal_ttl_s: int = 0,
    ) -> None:
        if client is None:
            if url is None:
                raise ValueError("either url or client required")
            client = redis.from_url(url, decode_responses=True)
        self._client = client
        self._allow_list = frozenset(s.lower() for s in allow_list)
        self._signal_ttl_s = signal_ttl_s

    @property
    def allow_list(self) -> frozenset[str]:
        return self._allow_list

    @staticmethod
    def _meta_key(domain: str) -> str:
        return f"urlintel:meta:{domain}"

    def _is_allow_listed(self, domain: str) -> bool:
        if domain in self._allow_list:
            return True
        return any(domain.endswith("." + a) for a in self._allow_list)

    async def add(
        self,
        *,
        domain: str,
        source: str,
        category: str = "unknown",
        confidence: float = 0.9,
        ttl_s: int | None = None,
    ) -> tuple[bool, str]:
        """Add a domain to the blocklist.

        Returns (added, reason). `added=False` is normal for either:
        - The domain is allow-listed (`reason="allow_listed"`), or
        - The domain is not a valid domain (`reason="invalid_domain"`).
        """
        d = normalise_domain(domain)
        if not d or not _DOMAIN_RE.match(d):
            return False, "invalid_domain"
        if self._is_allow_listed(d):
            return False, "allow_listed"
        await self._client.sadd(self.SET_KEY, d)
        meta = {
            "source": source,
            "category": category,
            "confidence": str(confidence),
            "added_at_ms": str(int(time.time() * 1000)),
        }
        await self._client.hset(self._meta_key(d), mapping=meta)
        # Apply TTL only when caller explicitly requests it (signals path).
        ttl = ttl_s if ttl_s is not None else 0
        if ttl > 0:
            await self._client.expire(self._meta_key(d), ttl)
            # Note: the SET membership has no TTL primitive in Redis — we
            # rely on the meta-TTL + a janitor job (Phase 2) to evict
            # expired domains from the SET. For Phase 1 the domain stays.
        return True, "added"

    async def remove(self, domain: str) -> bool:
        d = normalise_domain(domain)
        if not d:
            return False
        n = await self._client.srem(self.SET_KEY, d)
        await self._client.delete(self._meta_key(d))
        return n > 0

    async def check(self, value: str) -> CheckResult:
        d = normalise_domain(value)
        if not d:
            return CheckResult(blocked=False, domain="", matched=None, entry=None, allow_listed=False)
        if self._is_allow_listed(d):
            return CheckResult(blocked=False, domain=d, matched=None, entry=None, allow_listed=True)
        # Exact match wins.
        if await self._client.sismember(self.SET_KEY, d):
            entry = await self._read_entry(d)
            return CheckResult(blocked=True, domain=d, matched=d, entry=entry, allow_listed=False)
        # Subdomain — walk up the dotted parts.
        parts = d.split(".")
        for i in range(1, len(parts) - 1):
            parent = ".".join(parts[i:])
            if await self._client.sismember(self.SET_KEY, parent):
                entry = await self._read_entry(parent)
                return CheckResult(
                    blocked=True, domain=d, matched=parent, entry=entry, allow_listed=False
                )
        return CheckResult(blocked=False, domain=d, matched=None, entry=None, allow_listed=False)

    async def export_all(self) -> list[str]:
        members = await self._client.smembers(self.SET_KEY)
        return sorted(members)

    async def _read_entry(self, domain: str) -> BlocklistEntry | None:
        m = await self._client.hgetall(self._meta_key(domain))
        if not m:
            return BlocklistEntry(
                domain=domain, source="unknown", category="unknown", confidence=0.0, added_at_ms=0
            )
        try:
            return BlocklistEntry(
                domain=domain,
                source=str(m.get("source", "unknown")),
                category=str(m.get("category", "unknown")),
                confidence=float(m.get("confidence", "0") or 0),
                added_at_ms=int(m.get("added_at_ms", "0") or 0),
            )
        except (ValueError, TypeError):
            return None

    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        except Exception:  # noqa: BLE001
            pass


# In-memory implementation for tests and dev mode without Redis.


class _InMemoryClient:
    def __init__(self) -> None:
        self._sets: dict[str, set[str]] = {}
        self._hashes: dict[str, dict[str, str]] = {}

    async def sadd(self, name: str, *values: str) -> int:
        s = self._sets.setdefault(name, set())
        before = len(s)
        s.update(values)
        return len(s) - before

    async def srem(self, name: str, *values: str) -> int:
        s = self._sets.get(name)
        if not s:
            return 0
        before = len(s)
        for v in values:
            s.discard(v)
        return before - len(s)

    async def sismember(self, name: str, value: str) -> bool:
        return value in self._sets.get(name, set())

    async def smembers(self, name: str) -> set[str]:
        return set(self._sets.get(name, set()))

    async def hset(self, name: str, mapping: dict[str, str]) -> int:
        d = self._hashes.setdefault(name, {})
        added = sum(1 for k in mapping if k not in d)
        d.update(mapping)
        return added

    async def hgetall(self, name: str) -> dict[str, str]:
        return dict(self._hashes.get(name, {}))

    async def delete(self, *names: str) -> int:
        count = 0
        for n in names:
            if n in self._hashes:
                del self._hashes[n]
                count += 1
            if n in self._sets:
                del self._sets[n]
                count += 1
        return count

    async def expire(self, name: str, time: int) -> bool:
        # In-memory ignores TTL — fine for unit tests.
        return name in self._hashes

    async def aclose(self) -> None:
        return None


def in_memory_blocklist(*, allow_list: Iterable[str] = ()) -> Blocklist:
    return Blocklist(client=_InMemoryClient(), allow_list=allow_list)
