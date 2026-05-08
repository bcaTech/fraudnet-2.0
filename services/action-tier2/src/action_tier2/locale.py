"""Subscriber locale resolution for Tier-2 customer notifications.

The subscriber profile carries a `locale` column (CLAUDE.md / phase-2
schema). For Phase 1 we expose a Protocol so the runner can inject:

  - StaticLocaleResolver: returns DEFAULT_LOCALE for everyone (dev).
  - PostgresLocaleResolver: looks up the subscriber profile row.

The resolver is sync-friendly via a small async cache.
"""

from __future__ import annotations

from typing import Protocol

from fraudnet.i18n import DEFAULT_LOCALE, SUPPORTED_LOCALES


class SubscriberLocaleResolver(Protocol):
    async def resolve(self, msisdn: str) -> str: ...


class StaticLocaleResolver:
    """Always returns the configured default locale (English in Phase 1)."""

    def __init__(self, *, default: str = DEFAULT_LOCALE) -> None:
        if default not in SUPPORTED_LOCALES:
            raise ValueError(f"unsupported default locale: {default}")
        self._default = default

    async def resolve(self, msisdn: str) -> str:
        return self._default


class MappingLocaleResolver:
    """Test fixture — explicit msisdn → locale mapping with default fallback."""

    def __init__(
        self,
        *,
        mapping: dict[str, str] | None = None,
        default: str = DEFAULT_LOCALE,
    ) -> None:
        self._mapping = mapping or {}
        self._default = default

    async def resolve(self, msisdn: str) -> str:
        loc = self._mapping.get(msisdn, self._default)
        if loc not in SUPPORTED_LOCALES:
            return self._default
        return loc
