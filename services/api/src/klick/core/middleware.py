"""Middleware ASGI: request_id y registro de peticiones."""

import time
import uuid

import structlog
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"
_MAX_LEN = 128


def _valid(value: str | None) -> bool:
    return value is not None and 0 < len(value) <= _MAX_LEN and value.isprintable()


class RequestIdMiddleware:
    """ASGI puro (no BaseHTTPMiddleware) para que request.state y los errores funcionen bien."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = incoming if _valid(incoming) and incoming else str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        log = structlog.get_logger("klick.access")
        started = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            log.info(
                "request",
                method=scope["method"],
                path=scope["path"],
                status=status_holder["status"],
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
