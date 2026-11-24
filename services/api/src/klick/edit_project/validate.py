"""Validación del documento de edición (``EditProject``), en tres capas:

1. **JSON Schema** (la fuente única): estructura, rangos y tipos. Rechaza lo que los modelos
   generados dejarían pasar (p. ej. ``null`` en un campo opcional).
2. **Pydantic** (generado del mismo esquema): tipa el documento para que el render y las
   herramientas lo lean sin ``dict``.
3. **Reglas semánticas**: lo que un esquema no puede expresar (referencias entre campos, duraciones,
   solapes). Hay una implementación equivalente en TypeScript; ambas se prueban con los mismos
   fixtures (``packages/video-project-schema/fixtures``).

Un documento inválido nunca se guarda (docs/07 §2): el servicio de clips llama a
:func:`parse_edit_project` en cada ``PATCH``.
"""

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as SchemaError
from jsonschema.exceptions import best_match
from pydantic import ValidationError

from klick.edit_project.models import (
    AudioItem,
    AudioTrack,
    EditProject,
    ImageItem,
    TextItem,
    VideoItem,
    VideoTrack,
)

SCHEMA_PATH = Path(__file__).with_name("edit-project.schema.json")
MAX_SCHEMA_ISSUES = 20
MAX_TOTAL_ITEMS = 2000
_EPS = 1e-9


@dataclass(frozen=True)
class Issue:
    """Un problema del documento. ``code`` es estable (la interfaz y el agente dependen de él)."""

    code: str
    path: str
    message: str


class EditProjectInvalid(Exception):
    def __init__(self, issues: list[Issue]) -> None:
        super().__init__("; ".join(f"{i.code}@{i.path}" for i in issues[:5]))
        self.issues = issues

    @property
    def codes(self) -> set[str]:
        return {i.code for i in self.issues}


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def _path(parts: Any) -> str:
    out = ""
    for p in parts:
        out += f"[{p}]" if isinstance(p, int) else f".{p}"
    return out.lstrip(".") or "$"


def _leaf(error: SchemaError) -> SchemaError:
    """Baja por `oneOf`/`anyOf` hasta el campo concreto que falla (no hasta la pista entera)."""
    while error.context:
        branches: dict[Any, list[SchemaError]] = {}
        for sub in error.context:
            branches.setdefault(sub.relative_schema_path[0], []).append(sub)
        # la rama que el documento casi cumple (la de menos errores) es la que importa al usuario
        closest = min(branches.values(), key=len)
        error = best_match(closest) or closest[0]
    return error


def schema_issues(raw: Any) -> list[Issue]:
    errors = sorted(
        (_leaf(e) for e in _validator().iter_errors(raw)),
        key=lambda e: list(map(str, e.absolute_path)),
    )
    return [
        Issue("schema", _path(e.absolute_path), e.message[:300]) for e in errors[:MAX_SCHEMA_ISSUES]
    ]


# ------------------------------------------------------------------------------ semántica


def effective_ms(item: VideoItem | AudioItem) -> int:
    """Duración del ítem en la línea de tiempo (el recorte de la fuente dividido por la velocidad)."""
    speed = item.speed if item.speed is not None else 1.0
    # mitades hacia arriba (no al par de `round`): TypeScript usa la misma regla
    return math.floor((item.source_out_ms - item.source_in_ms) / speed + 0.5)


def _items(track: Any) -> list[Any]:
    """Elementos de una pista (cada tipo de pista tiene su propio tipo de elemento)."""
    return list(track.items)


def semantic_issues(doc: EditProject) -> list[Issue]:
    issues: list[Issue] = []
    add = issues.append
    sources = {s.id: s for s in doc.sources}
    duration = doc.timeline.duration_ms

    # identificadores únicos
    seen: set[str] = set()
    for i, s in enumerate(doc.sources):
        if s.id in seen:
            add(Issue("duplicate_id", f"sources[{i}].id", f"Fuente repetida: {s.id}"))
        seen.add(s.id)
    seen_tracks: set[str] = set()
    seen_items: set[str] = set()
    total = 0
    for ti, track in enumerate(doc.timeline.tracks):
        if track.id in seen_tracks:
            add(Issue("duplicate_id", f"timeline.tracks[{ti}].id", f"Pista repetida: {track.id}"))
        seen_tracks.add(track.id)
        for ii, item in enumerate(_items(track)):
            total += 1
            if item.id in seen_items:
                add(
                    Issue(
                        "duplicate_id",
                        f"timeline.tracks[{ti}].items[{ii}].id",
                        f"Elemento repetido: {item.id}",
                    )
                )
            seen_items.add(item.id)
    if total > MAX_TOTAL_ITEMS:
        add(Issue("too_many_items", "timeline", f"Máximo {MAX_TOTAL_ITEMS} elementos."))

    for ti, track in enumerate(doc.timeline.tracks):
        base = f"timeline.tracks[{ti}]"
        spans: list[tuple[int, int, int]] = []  # (inicio, fin, índice) para detectar solapes
        for ii, item in enumerate(_items(track)):
            where = f"{base}.items[{ii}]"
            if isinstance(item, VideoItem | AudioItem):
                _media_item(item, where, sources, duration, issues, spans, ii, track)
            elif isinstance(item, TextItem):
                _span(item.start_ms, item.end_ms, where, duration, issues)
            elif isinstance(item, ImageItem):
                _source(item.source_id, ("image",), where, sources, issues)
                _span(item.start_ms, item.end_ms, where, duration, issues)
        if isinstance(track, VideoTrack | AudioTrack):
            spans.sort()
            for (_, prev_end, _), (start, _, idx) in zip(spans, spans[1:], strict=False):
                if start < prev_end:
                    add(
                        Issue(
                            "track_overlap",
                            f"{base}.items[{idx}]",
                            "Los elementos de una pista no pueden solaparse.",
                        )
                    )

    _layout(doc, sources, issues)
    return issues


def _source(
    source_id: str,
    kinds: tuple[str, ...],
    where: str,
    sources: dict[str, Any],
    issues: list[Issue],
) -> None:
    source = sources.get(source_id)
    if source is None:
        issues.append(
            Issue("unknown_source", f"{where}.source_id", f"No existe la fuente {source_id}.")
        )
    elif source.kind not in kinds:
        issues.append(
            Issue(
                "source_kind_mismatch",
                f"{where}.source_id",
                f"La fuente {source_id} es {source.kind}; se esperaba {' o '.join(kinds)}.",
            )
        )


def _span(start: int, end: int, where: str, duration: int, issues: list[Issue]) -> bool:
    if end <= start:
        issues.append(Issue("invalid_range", where, "El fin debe ser posterior al inicio."))
        return False
    if end > duration:
        issues.append(
            Issue("exceeds_duration", where, "El elemento termina después de la línea de tiempo.")
        )
    return True


def _media_item(
    item: VideoItem | AudioItem,
    where: str,
    sources: dict[str, Any],
    duration: int,
    issues: list[Issue],
    spans: list[tuple[int, int, int]],
    index: int,
    track: Any,
) -> None:
    kinds = ("video",) if isinstance(track, VideoTrack) else ("audio", "video")
    _source(item.source_id, kinds, where, sources, issues)
    if item.source_out_ms <= item.source_in_ms:
        issues.append(
            Issue("invalid_range", where, "La salida de la fuente debe ser posterior a la entrada.")
        )
        return
    length = effective_ms(item)
    end = item.start_ms + length
    spans.append((item.start_ms, end, index))
    if end > duration:
        issues.append(
            Issue("exceeds_duration", where, "El elemento termina después de la línea de tiempo.")
        )
    fade_in = item.fade_in_ms or 0
    fade_out = item.fade_out_ms or 0
    if fade_in + fade_out > length:
        issues.append(Issue("fade_too_long", where, "Los fundidos suman más que el elemento."))
    if isinstance(item, VideoItem):
        if item.crop is not None and (
            item.crop.x + item.crop.w > 1 + _EPS or item.crop.y + item.crop.h > 1 + _EPS
        ):
            issues.append(
                Issue("crop_out_of_bounds", f"{where}.crop", "El recorte se sale del cuadro.")
            )
        previous = -1
        for ki, kf in enumerate(item.keyframes or []):
            if kf.t_ms <= previous:
                issues.append(
                    Issue(
                        "keyframes_unordered",
                        f"{where}.keyframes[{ki}]",
                        "Los tiempos deben crecer.",
                    )
                )
            if kf.t_ms > length:
                issues.append(
                    Issue(
                        "keyframe_out_of_range", f"{where}.keyframes[{ki}]", "Fuera del elemento."
                    )
                )
            previous = max(previous, kf.t_ms)


def _layout(doc: EditProject, sources: dict[str, Any], issues: list[Issue]) -> None:
    layout = doc.layout
    if layout.mode == "split":
        if layout.split is None:
            issues.append(
                Issue("layout_missing_config", "layout.split", "El modo split exige `split`.")
            )
        else:
            top, bottom = layout.split.top_source, layout.split.bottom_source
            ok = top != bottom and all(
                sources.get(s) is not None and sources[s].kind == "video" for s in (top, bottom)
            )
            if not ok:
                issues.append(
                    Issue(
                        "layout_unknown_source",
                        "layout.split",
                        "Las fuentes superior e inferior deben ser dos videos distintos del documento.",
                    )
                )
    if layout.mode == "fit" and layout.fit is None:
        issues.append(Issue("layout_missing_config", "layout.fit", "El modo fit exige `fit`."))


# --------------------------------------------------------------------------- punto de entrada


def parse_edit_project(raw: Any) -> EditProject:
    """Valida y tipa un documento. Lanza :class:`EditProjectInvalid` con todos los problemas."""
    issues = schema_issues(raw)
    if issues:
        raise EditProjectInvalid(issues)
    try:
        doc = EditProject.model_validate(raw)
    except ValidationError as exc:  # el esquema ya lo aceptó: indica un desajuste de generación
        raise EditProjectInvalid(
            [
                Issue("schema", _path(e["loc"]), str(e["msg"])[:300])
                for e in exc.errors()[:MAX_SCHEMA_ISSUES]
            ]
        ) from None
    problems = semantic_issues(doc)
    if problems:
        raise EditProjectInvalid(problems)
    return doc
