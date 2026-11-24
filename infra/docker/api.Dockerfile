# Imágenes de Klick (ADR-021: los workers corren en contenedor). Dos destinos:
#   --target api    API, worker-default/ai/publish y scheduler (sin FFmpeg).
#   --target media  worker-media: la misma base + FFmpeg/FFprobe fijados por versión y huella.
#
# FFmpeg 7.1.1 estático con libass, libx264, freetype, harfbuzz y fontconfig. Se fija la huella
# (digest), no solo la etiqueta: una etiqueta puede reapuntar a otra compilación.
FROM mwader/static-ffmpeg:7.1.1@sha256:11a44711684c0b9f754c047dcd64235b8b52deab251bd0e0a86f22faa160749c AS ffmpeg

FROM python:3.12-slim AS api

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

# --------------------------------------------------------------------------------- media
FROM api AS media

USER root
COPY --from=ffmpeg /ffmpeg /usr/local/bin/ffmpeg
COPY --from=ffmpeg /ffprobe /usr/local/bin/ffprobe
# libass necesita fontconfig y al menos una fuente del sistema para subtítulos quemados.
RUN apt-get update     && apt-get install -y --no-install-recommends fontconfig fonts-dejavu-core     && rm -rf /var/lib/apt/lists/*     && fc-cache -f     && mkdir -p /work && chown klick /work
ENV KLICK_FFMPEG_VERSION=7.1.1
USER klick

# ----------------------------------------------------------------------------- media-test
# Solo para pruebas: la imagen media + dependencias de desarrollo + las pruebas. Ejecuta las
# pruebas con FFmpeg real (`pnpm test:media`); FFmpeg no existe fuera de esta imagen.
FROM media AS media-test
USER root
RUN uv sync --frozen --all-packages
COPY --chown=klick services/api/tests services/api/tests
USER klick
ENV KLICK_REQUIRE_FFMPEG=1
CMD ["pytest", "services/api/tests/test_media_ffmpeg.py", "services/api/tests/test_media_probe.py", "-q", "-p", "no:cacheprovider"]
