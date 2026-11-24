"""Documento de edición inicial de un clip, a partir del tramo elegido de un video. Lógica pura."""

from typing import Any

# Formatos de salida (docs/04 `render_presets`): alto y ancho del lienzo.
ASPECTS: dict[str, tuple[int, int]] = {
    "9x16": (1080, 1920),
    "16x9": (1920, 1080),
    "1x1": (1080, 1080),
    "4x5": (1080, 1350),
}
DEFAULT_ASPECT = "9x16"
DEFAULT_FPS = 30
MIN_CLIP_MS = 100
FIT_BACKGROUND = "#0B0D10"


def initial_document(
    *, asset_id: str, start_ms: int, end_ms: int, aspect: str = DEFAULT_ASPECT
) -> dict[str, Any]:
    """Un clip con un solo corte del video original, encajado en el lienzo con fondo sólido.

    Sin IA de reencuadre (F2-08) el modo ``fit`` es el único que no recorta al usuario; el editor
    o el agente pueden cambiarlo después. Se valida con ``parse_edit_project`` antes de guardarlo.
    """
    width, height = ASPECTS[aspect]
    return {
        "schema_version": "1.0",
        "canvas": {"width": width, "height": height, "fps": DEFAULT_FPS, "background": "#000000"},
        "sources": [{"id": "s1", "asset_id": asset_id, "kind": "video"}],
        "layout": {
            "mode": "fit",
            "fit": {"background": {"type": "solid", "color": FIT_BACKGROUND}},
        },
        "timeline": {
            "duration_ms": end_ms - start_ms,
            "tracks": [
                {
                    "id": "v1",
                    "kind": "video",
                    "items": [
                        {
                            "id": "i1",
                            "source_id": "s1",
                            "source_in_ms": start_ms,
                            "source_out_ms": end_ms,
                            "start_ms": 0,
                        }
                    ],
                }
            ],
        },
        "audio": {"normalize": {"enabled": True}},
    }
