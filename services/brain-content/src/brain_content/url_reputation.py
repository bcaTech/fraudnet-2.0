"""URL reputation lookup.

Phase 1 ships an in-memory blocklist seeded at startup (from a config map
in production, from a fixture in tests). Phase 2 swaps to the production
malicious-URL database described in spec §5.3.

The interface is a pure ReputationLookup; the implementation can swap.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterable

# Bare-domain extraction from a URL or a domain string.
_HOST_RE = re.compile(r"^(?:[a-z]+://)?([^/?#]+)", re.IGNORECASE)


def domain_of(url: str) -> str:
    m = _HOST_RE.match(url)
    return m.group(1).lower() if m else url.lower()


@dataclass(frozen=True)
class ReputationVerdict:
    """Verdict on a URL or domain."""

    confidence: float          # 0..1
    category: str | None       # e.g. 'phishing', 'malware', 'scam'
    source: str                # which list raised the hit


class ReputationLookup(ABC):
    @abstractmethod
    def check(self, url: str) -> ReputationVerdict | None: ...


class StaticBlocklist(ReputationLookup):
    """In-memory blocklist over both full URLs and bare domains."""

    def __init__(
        self,
        *,
        bad_domains: Iterable[str] = (),
        bad_urls: Iterable[str] = (),
        category: str = "phishing",
        source: str = "static-blocklist",
        confidence: float = 0.95,
    ) -> None:
        self._domains = {d.lower() for d in bad_domains}
        self._urls = {u.lower() for u in bad_urls}
        self._category = category
        self._source = source
        self._confidence = confidence

    def check(self, url: str) -> ReputationVerdict | None:
        u = url.lower()
        if u in self._urls:
            return ReputationVerdict(
                confidence=self._confidence,
                category=self._category,
                source=self._source,
            )
        host = domain_of(u)
        # Match bare domain or any subdomain of a flagged domain.
        if host in self._domains:
            return ReputationVerdict(
                confidence=self._confidence,
                category=self._category,
                source=self._source,
            )
        for d in self._domains:
            if host.endswith("." + d):
                return ReputationVerdict(
                    confidence=self._confidence * 0.9,
                    category=self._category,
                    source=self._source,
                )
        return None
