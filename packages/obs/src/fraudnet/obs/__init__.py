"""FraudNet 2.0 observability primitives.

Three pillars (CLAUDE.md §7.4): structured JSON logs, OTLP traces, Prometheus
metrics. Every service imports from here; nothing else gets to set up logging
or tracing.
"""

from fraudnet.obs.context import (
    bind_context,
    clear_context,
    get_request_id,
    new_request_id,
    set_request_id,
)
from fraudnet.obs.logging import configure_logging, get_logger
from fraudnet.obs.metrics import (
    counter,
    histogram,
    metrics_endpoint,
    observe_duration,
    request_duration,
    requests_total,
)
from fraudnet.obs.redact import redact, redact_mapping
from fraudnet.obs.tracing import configure_tracing, get_tracer, traced

__all__ = [
    # context
    "bind_context",
    "clear_context",
    "get_request_id",
    "new_request_id",
    "set_request_id",
    # logging
    "configure_logging",
    "get_logger",
    # metrics
    "counter",
    "histogram",
    "metrics_endpoint",
    "observe_duration",
    "request_duration",
    "requests_total",
    # redact
    "redact",
    "redact_mapping",
    # tracing
    "configure_tracing",
    "get_tracer",
    "traced",
]
