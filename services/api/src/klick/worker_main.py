"""Punto de entrada de los workers: ``dramatiq klick.worker_main --queues default``."""

import klick.jobs_handlers  # noqa: F401 - registra los kinds de job
from klick.core.config import get_settings
from klick.core.logging import configure_logging
from klick.core.queue import setup_broker

_settings = get_settings()
configure_logging(_settings.log_level, _settings.log_json)
broker = setup_broker(_settings.redis_url)
