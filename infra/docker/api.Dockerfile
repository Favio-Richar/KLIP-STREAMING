# Imagen común de API, workers y scheduler (ADR-021: los workers corren en contenedor).
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_PREFERENCE=only-system \
    PATH="/app/.venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.11.0 /uv /usr/local/bin/uv

WORKDIR /app

# Dependencias primero (capa cacheable): solo cambian con los manifiestos.
COPY pyproject.toml uv.lock .python-version ./
COPY services/api/pyproject.toml services/api/pyproject.toml
RUN mkdir -p services/api/src/klick \
    && uv sync --frozen --no-dev --package klick --no-install-project

# Código de la aplicación.
COPY services/api/src services/api/src
COPY services/api/alembic services/api/alembic
COPY services/api/alembic.ini services/api/alembic.ini
RUN uv sync --frozen --no-dev --package klick

RUN useradd --system --uid 10001 --home /app klick && chown -R klick /app
USER klick

CMD ["uvicorn", "klick.main:app", "--host", "0.0.0.0", "--port", "8000"]
