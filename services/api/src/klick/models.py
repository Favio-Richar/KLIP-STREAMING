"""Registro de todos los modelos.

Las claves foráneas se resuelven por nombre dentro de un único ``MetaData``: un proceso que solo
importa una parte de los modelos falla al usar una tabla cuyas referencias no cargó (p. ej. un
worker que toca ``media_assets`` sin haber importado ``users``). Cada punto de entrada
(API, workers, scheduler) llama a :func:`load_all_models` al arrancar.
"""

from importlib import import_module

MODEL_MODULES = (
    "klick.core.audit",
    "klick.core.idempotency",
    "klick.core.jobs",
    "klick.core.outbox",
    "klick.identity.models",
    "klick.orgs.models",
    "klick.storage.models",
    "klick.media.models",
    "klick.clips.models",
    "klick.render.models",
)


def load_all_models() -> None:
    for name in MODEL_MODULES:
        import_module(name)
