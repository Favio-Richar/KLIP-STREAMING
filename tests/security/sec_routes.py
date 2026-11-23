"""Introspección de rutas: clasifica cada endpoint por el tipo de autenticación que exige.

Si se añade una ruta nueva, los tests de seguridad fallan hasta que se clasifique en
``sec_policy.py``. Así ningún endpoint queda fuera del arnés de IDOR y de la matriz de roles.
"""

from collections.abc import Callable, Iterator, Sequence
from typing import Any, Literal

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from klick.identity.deps import current_auth
from klick.orgs.deps import current_principal, path_principal

Kind = Literal["public", "user", "org_header", "org_path"]


def _calls(dep: Dependant) -> Iterator[Callable[..., object]]:
    for sub in dep.dependencies:
        if sub.call is not None:
            yield sub.call
        yield from _calls(sub)


def _walk(routes: Sequence[Any], prefix: str = "") -> Iterator[tuple[str, APIRoute]]:
    """Aplana las rutas. FastAPI recientes incluyen los routers de forma perezosa
    (``_IncludedRouter``), por eso se acumula el prefijo de cada inclusión."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield prefix + route.path, route
        elif hasattr(route, "original_router") and hasattr(route, "include_context"):
            extra = getattr(route.include_context, "prefix", "") or ""
            yield from _walk(route.original_router.routes, prefix + extra)
        elif hasattr(route, "routes"):
            yield from _walk(route.routes, prefix)


def classify_routes(app: FastAPI) -> dict[tuple[str, str], Kind]:
    out: dict[tuple[str, str], Kind] = {}
    for path, route in _walk(app.routes):
        calls = set(_calls(route.dependant))
        kind: Kind
        if current_principal in calls:
            kind = "org_header"
        elif path_principal in calls:
            kind = "org_path"
        elif current_auth in calls:
            kind = "user"
        else:
            kind = "public"
        for method in (route.methods or set()) - {"HEAD", "OPTIONS"}:
            out[(method, path)] = kind
    return out
