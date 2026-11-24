"""Logs estructurados con structlog. Nunca registrar credenciales ni contenido de usuarios."""

import logging
import sys

import structlog
from opentelemetry import trace
from structlog.typing import EventDict, Processor

_REDACTED_KEYS = ("password", "secret", "token", "authorization", "api_key", "cookie")


def _redact(_: object, __: str, event_dict: EventDict) -> EventDict:
    for key in list(event_dict):
        if any(marker in key.lower() for marker in _REDACTED_KEYS):
            event_dict[key] = "[redacted]"
    return event_dict


def trace_context(_: object, __: str, event_dict: EventDict) -> EventDict:
    """Añade trace_id/span_id si hay un span activo, para correlacionar logs y trazas."""
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def configure_logging(level: str = "INFO", json_logs: bool = False) -> None:
    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact,
        trace_context,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: Processor = (
        structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[*processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )
