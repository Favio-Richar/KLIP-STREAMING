"""Errores de API en application/problem+json (RFC 9457)."""

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_JSON = "application/problem+json"

_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    412: "precondition_failed",
    422: "validation_error",
    429: "rate_limited",
}


class ApiError(Exception):
    """Error de dominio con código estable (en inglés)."""

    def __init__(
        self,
        status: int,
        code: str,
        title: str,
        detail: str | None = None,
        headers: dict[str, str] | None = None,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(detail or title)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.headers = headers
        self.errors = errors


def problem_response(
    request: Request,
    *,
    status: int,
    code: str,
    title: str,
    detail: str | None = None,
    errors: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"https://klick.app/problems/{code}",
        "title": title,
        "status": status,
        "code": code,
        "request_id": getattr(request.state, "request_id", None),
    }
    if detail:
        body["detail"] = detail
    if errors:
        body["errors"] = errors
    out_headers = dict(headers or {})
    if body["request_id"]:
        # El handler de Exception corre fuera de RequestIdMiddleware: el encabezado se fija aquí.
        out_headers.setdefault("X-Request-ID", str(body["request_id"]))
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=out_headers)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return problem_response(
            request,
            status=exc.status,
            code=exc.code,
            title=exc.title,
            detail=exc.detail,
            errors=exc.errors,
            headers=exc.headers,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, f"http_{exc.status_code}")
        title = "Resource not found" if exc.status_code == 404 else str(exc.detail)
        return problem_response(
            request,
            status=exc.status_code,
            code=code,
            title=title,
            headers=dict(exc.headers) if exc.headers else None,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {
                "in": str(err["loc"][0]) if err["loc"] else "body",
                "field": ".".join(str(p) for p in err["loc"][1:]),
                "message": err["msg"],
                "type": err["type"],
            }
            for err in exc.errors()
        ]
        return problem_response(
            request,
            status=422,
            code="validation_error",
            title="Request validation failed",
            errors=errors,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        structlog.get_logger("klick.api").error(
            "unhandled_exception", exc_info=exc, path=request.url.path
        )
        return problem_response(
            request, status=500, code="internal_error", title="Internal server error"
        )
