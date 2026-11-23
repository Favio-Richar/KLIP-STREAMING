"""Contenedor de utilidades compartidas por las pruebas de API."""

from dataclasses import dataclass

import httpx
from fastapi import FastAPI
from klick.core.config import Settings
from klick.core.email import MemoryEmailSender
from redis.asyncio import Redis


@dataclass
class ApiHarness:
    app: FastAPI
    client: httpx.AsyncClient
    emails: MemoryEmailSender
    db_urls: dict[str, str]
    redis: Redis
    settings: Settings
