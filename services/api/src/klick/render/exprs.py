"""Piezas puras del grafo de FFmpeg: números, tiempos, rutas, colores y expresiones de animación.

Todo lo que viene del documento (texto, colores, ids) es dato no confiable: nada se interpola en el
grafo sin pasar por estas funciones, que solo producen un alfabeto cerrado de caracteres.
"""

import math
import re
from collections.abc import Sequence
from typing import Any

_SAFE_PATH = re.compile(r"^[A-Za-z0-9_./-]+$")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


class RenderPlanError(ValueError):
    """El documento no se puede convertir en un plan de render (entrada inconsistente)."""


def num(value: float, digits: int = 4) -> str:
    """Número decimal estable (sin notación científica, sin ceros sobrantes)."""
    if not math.isfinite(value):
        raise RenderPlanError(f"número no finito: {value!r}")
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def secs(ms: int | float) -> str:
    """Milisegundos → segundos con hasta 3 decimales (el grafo trabaja en segundos)."""
    return num(ms / 1000, 3)


def safe_path(path: str) -> str:
    """Una ruta que va dentro del grafo solo puede tener caracteres que no tienen significado ahí
    (sin comillas, dos puntos, comas, corchetes ni espacios). Las rutas las genera Klick, no el
    usuario; esto es una defensa por si alguien lo olvida."""
    if not _SAFE_PATH.match(path):
        raise RenderPlanError(f"ruta no permitida en el grafo: {path!r}")
    return path


def color(value: str) -> str:
    """`#RRGGBB` → `0xRRGGBB` (formato de FFmpeg). Rechaza cualquier otra cosa."""
    if not _COLOR.match(value):
        raise RenderPlanError(f"color inválido: {value!r}")
    return "0x" + value[1:].upper()


def atempo_chain(speed: float) -> list[str]:
    """`atempo` solo admite 0,5–2 por etapa: se encadenan para cubrir 0,25–4."""
    if not 0.25 <= speed <= 4:
        raise RenderPlanError(f"velocidad fuera de rango: {speed}")
    stages: list[str] = []
    remaining = speed
    while remaining > 2.0 + 1e-9:
        stages.append("atempo=2")
        remaining /= 2.0
    while remaining < 0.5 - 1e-9:
        stages.append("atempo=0.5")
        remaining /= 0.5
    if abs(remaining - 1.0) > 1e-9:
        stages.append(f"atempo={num(remaining, 6)}")
    return stages


def _ease(kind: str, p: str) -> str:
    """Curva de suavizado sobre p∈[0,1] como expresión de FFmpeg."""
    if kind == "in":
        return f"({p}*{p})"
    if kind == "out":
        return f"(1-(1-{p})*(1-{p}))"
    if kind == "inout":
        return f"if(lt({p},0.5),2*{p}*{p},1-pow(-2*{p}+2,2)/2)"
    return p


def interpolation(
    times_s: Sequence[float], values: Sequence[float], eases: Sequence[str], var: str = "it"
) -> str:
    """Expresión por tramos que interpola ``values`` entre ``times_s`` según el tiempo ``var``.

    Antes del primer instante vale el primer valor; después del último, el último. La curva de cada
    tramo es la del *keyframe de inicio* del tramo.
    """
    if not times_s or len(times_s) != len(values) or len(values) != len(eases):
        raise RenderPlanError("keyframes inconsistentes")
    if len(times_s) == 1:
        return num(values[0], 5)
    expr = num(values[-1], 5)
    for i in range(len(times_s) - 2, -1, -1):
        t0, t1 = times_s[i], times_s[i + 1]
        v0, v1 = values[i], values[i + 1]
        if t1 <= t0:
            raise RenderPlanError("los keyframes deben crecer en el tiempo")
        p = f"(({var}-{num(t0, 3)})/{num(t1 - t0, 3)})"
        segment = f"({num(v0, 5)}+({num(v1 - v0, 5)})*{_ease(eases[i], p)})"
        expr = f"if(lt({var},{num(t1, 3)}),{segment},{expr})"
    first = num(values[0], 5)
    return f"if(lt({var},{num(times_s[0], 3)}),{first},{expr})"


def even(value: float) -> int:
    """Entero par ≥ 2 (los códecs 4:2:0 exigen dimensiones pares)."""
    return max(2, int(round(value / 2.0)) * 2)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def as_float(value: Any, default: float) -> float:
    return default if value is None else float(value)
