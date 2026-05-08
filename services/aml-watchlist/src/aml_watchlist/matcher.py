"""Watchlist match engine — composes exact + fuzzy lookups.

`MatchEngine.check_*` returns the best match (or None) plus the score
and the matched entry. Callers compare against their threshold; the
engine itself does not gate.

For name matching, the engine pulls the active name corpus into memory
on startup and refreshes on a TTL. Phase 1 watchlist is small (<10k
entries); a Postgres trigram index would be the right next step when
volume crosses 100k.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from typing import Any

from fraudnet.obs import counter, get_logger, histogram

from aml_watchlist.db import MatchLogRepo, WatchlistRepo
from aml_watchlist.matching import (
    NameMatch,
    name_match_score,
    normalise,
)

_log = get_logger("aml_watchlist.matcher")
_MATCH_DURATION = histogram(
    "aml_watchlist_match_seconds",
    "Watchlist match duration.",
    labelnames=("kind",),
)
_HITS = counter(
    "aml_watchlist_hits_total",
    "Watchlist hits.",
    labelnames=("source", "kind"),
)


@dataclass(frozen=True)
class MatchResult:
    hit: bool
    score: float
    threshold: float
    entry: dict[str, Any] | None
    explanation: NameMatch | None  # only populated for fuzzy name matches


class MatchEngine:
    def __init__(
        self,
        *,
        repo: WatchlistRepo,
        match_log: MatchLogRepo,
        threshold: float = 0.85,
        cache_ttl_s: int = 300,
    ) -> None:
        self._repo = repo
        self._log_repo = match_log
        self._threshold = threshold
        self._cache_ttl_s = cache_ttl_s
        self._name_cache: list[dict[str, Any]] = []
        self._normalised_cache: list[str] = []
        self._cache_loaded_at: float = 0.0
        self._cache_lock = asyncio.Lock()

    @property
    def threshold(self) -> float:
        return self._threshold

    async def _ensure_cache(self) -> None:
        now = time.time()
        if self._name_cache and (now - self._cache_loaded_at) < self._cache_ttl_s:
            return
        async with self._cache_lock:
            if self._name_cache and (time.time() - self._cache_loaded_at) < self._cache_ttl_s:
                return
            entries = await self._repo.list_active_names()
            self._name_cache = entries
            # Pre-normalise names for faster matching (collapse whitespace,
            # strip diacritics) — the matching primitive does this each call,
            # but storing normalised once lets us short-circuit clearly-non-
            # matching candidates by length / first-char.
            self._normalised_cache = [normalise(e["name"]) for e in entries]
            self._cache_loaded_at = time.time()

    async def check_msisdn(
        self, msisdn: str, *, caller: str | None = None
    ) -> MatchResult:
        with _MATCH_DURATION.labels(kind="msisdn").time():
            rows = await self._repo.find_by_msisdn(msisdn)
        if rows:
            entry = rows[0]
            await self._log_repo.log(
                query_kind="msisdn",
                query_value_hash=_hash(msisdn),
                matched_entry_id=entry["id"],
                match_score=1.0,
                threshold=self._threshold,
                outcome="hit",
                caller=caller,
            )
            _HITS.labels(source=entry["source"], kind="msisdn").inc()
            return MatchResult(
                hit=True,
                score=1.0,
                threshold=self._threshold,
                entry=entry,
                explanation=None,
            )
        await self._log_repo.log(
            query_kind="msisdn",
            query_value_hash=_hash(msisdn),
            matched_entry_id=None,
            match_score=0.0,
            threshold=self._threshold,
            outcome="miss",
            caller=caller,
        )
        return MatchResult(
            hit=False, score=0.0, threshold=self._threshold, entry=None, explanation=None
        )

    async def check_national_id(
        self, national_id: str, *, caller: str | None = None
    ) -> MatchResult:
        with _MATCH_DURATION.labels(kind="national_id").time():
            rows = await self._repo.find_by_national_id(national_id)
        if rows:
            entry = rows[0]
            await self._log_repo.log(
                query_kind="national_id",
                query_value_hash=_hash(national_id),
                matched_entry_id=entry["id"],
                match_score=1.0,
                threshold=self._threshold,
                outcome="hit",
                caller=caller,
            )
            _HITS.labels(source=entry["source"], kind="national_id").inc()
            return MatchResult(
                hit=True,
                score=1.0,
                threshold=self._threshold,
                entry=entry,
                explanation=None,
            )
        await self._log_repo.log(
            query_kind="national_id",
            query_value_hash=_hash(national_id),
            matched_entry_id=None,
            match_score=0.0,
            threshold=self._threshold,
            outcome="miss",
            caller=caller,
        )
        return MatchResult(
            hit=False, score=0.0, threshold=self._threshold, entry=None, explanation=None
        )

    async def check_name(
        self, name: str, *, caller: str | None = None
    ) -> MatchResult:
        await self._ensure_cache()
        with _MATCH_DURATION.labels(kind="name").time():
            best_score = 0.0
            best_entry: dict[str, Any] | None = None
            best_match: NameMatch | None = None
            for entry in self._name_cache:
                # Try the primary name + any alias; take the highest.
                candidates = [entry["name"]] + list(entry.get("aliases") or [])
                for cand in candidates:
                    m = name_match_score(name, cand)
                    if m.score > best_score:
                        best_score = m.score
                        best_entry = entry
                        best_match = m
                        if best_score >= 0.99:
                            break
                if best_score >= 0.99:
                    break

        hit = best_score >= self._threshold and best_entry is not None
        await self._log_repo.log(
            query_kind="name",
            query_value_hash=_hash(name),
            matched_entry_id=best_entry["id"] if hit and best_entry else None,
            match_score=best_score,
            threshold=self._threshold,
            outcome="hit" if hit else "miss",
            caller=caller,
        )
        if hit and best_entry is not None:
            _HITS.labels(source=best_entry["source"], kind="name").inc()
        return MatchResult(
            hit=hit,
            score=best_score,
            threshold=self._threshold,
            entry=best_entry if hit else None,
            explanation=best_match if hit else None,
        )


def _hash(value: str) -> str:
    """Stable hash for the audit log. We never log raw query values
    because they may carry PII (MSISDNs, names)."""
    return hashlib.sha256(value.encode()).hexdigest()[:16]
