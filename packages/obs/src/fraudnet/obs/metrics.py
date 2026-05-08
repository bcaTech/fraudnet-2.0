"""Prometheus metric helpers.

Conventions per CLAUDE.md §7.4:
  - RED per endpoint: requests_total (Counter), request_duration (Histogram).
  - USE per resource: utilisation, saturation, errors.
  - Custom business KPIs go through `counter()` / `histogram()` factories.

Service code should not instantiate Prometheus primitives directly. Use the
helpers here so labelnames stay consistent across services.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, contextmanager
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)

# Each service has its own registry; the FastAPI /metrics endpoint exposes it.
_REGISTRY = CollectorRegistry(auto_describe=True)

# Buckets tuned for telco / API workloads. The 5–25 ms band covers Tier 1
# inline scoring; the 250 ms–1 s band covers NOC API queries; longer covers
# batch / replay workloads.
_DURATION_BUCKETS = (
    0.001,
    0.005,
    0.010,
    0.025,
    0.050,
    0.100,
    0.250,
    0.500,
    1.000,
    2.500,
    5.000,
    10.000,
)


requests_total = Counter(
    "fraudnet_requests_total",
    "Total requests handled, by service / route / method / status.",
    labelnames=("service", "route", "method", "status"),
    registry=_REGISTRY,
)


request_duration = Histogram(
    "fraudnet_request_duration_seconds",
    "Request duration in seconds, by service / route / method / status.",
    labelnames=("service", "route", "method", "status"),
    buckets=_DURATION_BUCKETS,
    registry=_REGISTRY,
)


def counter(name: str, doc: str, labelnames: tuple[str, ...] = ()) -> Counter:
    return Counter(name, doc, labelnames=labelnames, registry=_REGISTRY)


def histogram(
    name: str,
    doc: str,
    labelnames: tuple[str, ...] = (),
    buckets: tuple[float, ...] = _DURATION_BUCKETS,
) -> Histogram:
    return Histogram(name, doc, labelnames=labelnames, buckets=buckets, registry=_REGISTRY)


@contextmanager
def observe_duration(hist: Histogram, **labels: str) -> Any:
    """Record the duration of a sync block on a histogram."""
    start = time.perf_counter()
    try:
        yield
    finally:
        hist.labels(**labels).observe(time.perf_counter() - start)


@asynccontextmanager
async def observe_duration_async(hist: Histogram, **labels: str) -> AsyncIterator[None]:
    start = time.perf_counter()
    try:
        yield
    finally:
        hist.labels(**labels).observe(time.perf_counter() - start)


def metrics_endpoint() -> Callable[[], tuple[bytes, str]]:
    """Return a function that produces (body, content_type) for /metrics."""

    def _render() -> tuple[bytes, str]:
        return generate_latest(_REGISTRY), CONTENT_TYPE_LATEST

    return _render
