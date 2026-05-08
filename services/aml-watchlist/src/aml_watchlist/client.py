"""AML watchlist client — used by brain-behavioural and brain-content
to enrich scoring with watchlist hits.

The client is async, fail-soft (an unreachable watchlist must not break
scoring), and emits a `aml.watchlist_match` signal_kind onto the same
fraud.signals.v1 topic when there's a match.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

from fraudnet.obs import counter, get_logger

_log = get_logger("aml_watchlist.client")
_CHECKS = counter(
    "aml_watchlist_client_checks_total",
    "Checks against the AML watchlist service.",
    labelnames=("kind", "outcome"),
)


@dataclass(frozen=True)
class WatchlistHit:
    hit: bool
    score: float
    threshold: float
    matched_entry_id: str | None = None
    matched_name: str | None = None
    source: str | None = None
    category: str | None = None


class WatchlistClient(Protocol):
    async def check_msisdn(self, msisdn: str) -> WatchlistHit: ...

    async def check_name(self, name: str) -> WatchlistHit: ...


class HttpWatchlistClient:
    def __init__(
        self,
        *,
        base_url: str,
        caller: str = "brain-behavioural",
        timeout_s: float = 1.5,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._caller = caller
        self._timeout_s = timeout_s
        self._client = httpx.AsyncClient(
            base_url=self._base,
            timeout=timeout_s,
            headers={"X-Caller-Service": caller},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def check_msisdn(self, msisdn: str) -> WatchlistHit:
        return await self._check("msisdn", msisdn)

    async def check_name(self, name: str) -> WatchlistHit:
        return await self._check("name", name)

    async def check_national_id(self, national_id: str) -> WatchlistHit:
        return await self._check("national_id", national_id)

    async def _check(self, kind: str, value: str) -> WatchlistHit:
        try:
            resp = await self._client.get(f"/watchlist/check/{kind}/{value}")
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            _CHECKS.labels(kind=kind, outcome="transport_error").inc()
            _log.warning("aml_watchlist.client.error", kind=kind, error=str(exc))
            return WatchlistHit(hit=False, score=0.0, threshold=0.0)
        body = resp.json()
        outcome = "hit" if body.get("hit") else "miss"
        _CHECKS.labels(kind=kind, outcome=outcome).inc()
        return WatchlistHit(
            hit=bool(body.get("hit", False)),
            score=float(body.get("score", 0.0)),
            threshold=float(body.get("threshold", 0.0)),
            matched_entry_id=body.get("matched_entry_id"),
            matched_name=body.get("matched_name"),
            source=body.get("source"),
            category=body.get("category"),
        )


class NoopWatchlistClient:
    """Used in tests / when AML is disabled."""

    async def check_msisdn(self, msisdn: str) -> WatchlistHit:
        return WatchlistHit(hit=False, score=0.0, threshold=0.0)

    async def check_name(self, name: str) -> WatchlistHit:
        return WatchlistHit(hit=False, score=0.0, threshold=0.0)

    async def close(self) -> None:
        return None
