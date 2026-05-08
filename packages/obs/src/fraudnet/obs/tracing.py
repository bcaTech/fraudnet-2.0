"""OpenTelemetry tracing wiring.

CLAUDE.md §7.4: 1% sampling in prod, 100% on error. Every request traced from
api-public down through every service it calls. Spans on Kafka producers /
consumers and DB calls.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import (
    ALWAYS_ON,
    ParentBased,
    Sampler,
    TraceIdRatioBased,
)

P = ParamSpec("P")
T = TypeVar("T")


def _build_sampler() -> Sampler:
    ratio = float(os.environ.get("OTEL_SAMPLE_RATIO", "0.01"))
    if os.environ.get("FRAUDNET_ENV", "dev") == "dev":
        return ALWAYS_ON
    return ParentBased(root=TraceIdRatioBased(ratio))


def configure_tracing(*, service: str, version: str = "0.0.0") -> None:
    """Configure global tracer provider. Idempotent — call once at startup."""
    if isinstance(trace.get_tracer_provider(), TracerProvider):
        # Already configured.
        return
    resource = Resource.create(
        {
            "service.name": service,
            "service.version": version,
            "deployment.environment": os.environ.get("FRAUDNET_ENV", "dev"),
        }
    )
    provider = TracerProvider(resource=resource, sampler=_build_sampler())
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)


def get_tracer(name: str) -> trace.Tracer:
    return trace.get_tracer(name)


def traced(
    span_name: str | None = None,
    *,
    attributes: dict[str, Any] | None = None,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Async decorator: wrap a coroutine in a span.

    Records exceptions and sets span status to error on raise. Use sparingly
    in hot paths — the tracer overhead is small but non-zero.
    """

    def decorator(fn: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        name = span_name or f"{fn.__module__}.{fn.__qualname__}"

        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            tracer = trace.get_tracer(fn.__module__)
            with tracer.start_as_current_span(name) as span:
                if attributes:
                    for k, v in attributes.items():
                        span.set_attribute(k, v)
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(trace.StatusCode.ERROR, str(exc))
                    raise

        return wrapper

    return decorator
