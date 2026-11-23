"""Logs estructurados con structlog. Nunca registrar credenciales ni contenido de usuarios."""

import logging
import sys

import structlog
from structlog.typing import EventDict, Processor

_REDACTED_KEYS = ("password", "secret", "token", "authorization", "api_key", "cookie")


def _redact(_: object, __: str, event_dict: EventDict) -> EventDict:
    for key in list(event_dict):
        if any(marker in key.lower() for marker in _REDACTED_KEYS):
            event_dict[key] = "[redacted]"
    return event_dict


def configure_logging(level: str = "INFO", json_logs: bool = False) -> None:
    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact,
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
