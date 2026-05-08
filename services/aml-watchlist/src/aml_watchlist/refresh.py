"""Periodic refresh task — pulls UN + OFAC and applies atomic-replace.

Runs as an asyncio task on the service. On each tick:
  1. Fetch the public feed.
  2. Parse to canonical rows.
  3. Atomic-replace (deactivate prior rows in this source, insert new).
  4. Update watchlist_sources status.

Failures are non-fatal — a feed that's down on one tick is logged and
retried on the next. The previously-active rows remain active until a
successful refresh, so detection coverage does not regress on transient
errors.
"""

from __future__ import annotations

import asyncio
import time
from uuid import uuid4

from fraudnet.obs import counter, get_logger

from aml_watchlist.db import WatchlistRepo
from aml_watchlist.feeds import (
    fetch_text,
    parse_ofac_csv,
    parse_un_xml,
)

_log = get_logger("aml_watchlist.refresh")
_REFRESHES = counter(
    "aml_watchlist_refresh_runs_total",
    "Watchlist feed refresh runs.",
    labelnames=("source", "outcome"),
)


class RefreshScheduler:
    def __init__(
        self,
        *,
        repo: WatchlistRepo,
        un_url: str,
        ofac_url: str,
        interval_s: int = 86_400,
    ) -> None:
        self._repo = repo
        self._un_url = un_url
        self._ofac_url = ofac_url
        self._interval_s = interval_s
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="aml-refresh")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task

    async def _loop(self) -> None:
        # Initial delay so the service is healthy before the first refresh.
        await asyncio.sleep(min(60, self._interval_s))
        while not self._stop.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_s)
            except TimeoutError:
                continue

    async def run_once(self) -> None:
        await asyncio.gather(
            self._refresh_un(),
            self._refresh_ofac(),
            return_exceptions=True,
        )

    async def _refresh_un(self) -> None:
        try:
            text = await fetch_text(self._un_url, timeout_s=60.0)
            rows = parse_un_xml(text)
            count = await self._repo.replace_source(
                source="un",
                refresh_id=f"un-{int(time.time())}-{uuid4().hex[:6]}",
                rows=rows,
            )
            _REFRESHES.labels(source="un", outcome="success").inc()
            _log.info("aml_watchlist.refresh.un.success", count=count)
        except Exception as exc:  # noqa: BLE001
            _REFRESHES.labels(source="un", outcome="failure").inc()
            _log.warning("aml_watchlist.refresh.un.failed", error=str(exc))

    async def _refresh_ofac(self) -> None:
        try:
            text = await fetch_text(self._ofac_url, timeout_s=60.0)
            rows = parse_ofac_csv(text)
            count = await self._repo.replace_source(
                source="ofac",
                refresh_id=f"ofac-{int(time.time())}-{uuid4().hex[:6]}",
                rows=rows,
            )
            _REFRESHES.labels(source="ofac", outcome="success").inc()
            _log.info("aml_watchlist.refresh.ofac.success", count=count)
        except Exception as exc:  # noqa: BLE001
            _REFRESHES.labels(source="ofac", outcome="failure").inc()
            _log.warning("aml_watchlist.refresh.ofac.failed", error=str(exc))
