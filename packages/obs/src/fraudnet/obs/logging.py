"""Structured JSON logging.

Every service calls `configure_logging()` once at startup. Output is JSON,
one record per line, with `timestamp`, `level`, `service`, `request_id`,
`message` always present. PII fields are auto-redacted (CLAUDE.md §7.4).
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog

from fraudnet.obs.context import context_processor
from fraudnet.obs.redact import _PII_FIELD_NAMES, redact

_DEFAULT_LEVEL = "INFO"


def _redact_processor(_logger: object, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key in list(event_dict.keys()):
        if key.lower() in _PII_FIELD_NAMES:
            event_dict[key] = redact(event_dict[key])
    return event_dict


def _service_processor(service: str) -> Any:
    def add_service(_logger: object, _name: str, ed: dict[str, Any]) -> dict[str, Any]:
        ed.setdefault("service", service)
        return ed

    return add_service


def configure_logging(
    *,
    service: str,
    level: str | int | None = None,
    json_output: bool | None = None,
) -> None:
    """Wire up structlog + stdlib logging.

    Args:
        service: short name of the calling service (e.g. 'ingest-momo').
        level:   log level. Defaults to env LOG_LEVEL or INFO.
        json_output: force JSON renderer (default in prod) or pretty
                     console renderer (default when stdout is a TTY).
    """
    level = level or os.environ.get("LOG_LEVEL", _DEFAULT_LEVEL)
    if json_output is None:
        json_output = not sys.stdout.isatty()

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        context_processor,
        _service_processor(service),
        structlog.processors.add_log_level,
        timestamper,
        _redact_processor,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_output:
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(
                level.upper() if isinstance(level, str) else logging.getLevelName(level),
                logging.INFO,
            )
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Bridge stdlib logging through structlog so libraries like FastAPI /
    # uvicorn / kafka emit JSON too.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level if isinstance(level, int) else logging.getLevelName(level.upper()))


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    return structlog.get_logger(name)
