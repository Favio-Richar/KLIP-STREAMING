"""Generación del archivo ASS de subtítulos (docs/07 §3). Puro y determinista.

Los textos de los cues son contenido del usuario: nunca se interpolan tal cual. En ASS las llaves
abren bloques de efectos (`{\\pos(..)}`) y la barra invertida inicia comandos, así que se
sustituyen por caracteres inertes y los saltos de línea se escriben como `\\N`. El archivo se
escribe en disco y FFmpeg lo lee con `ass=filename=` (la ruta la fija el sistema, no el usuario).

Estilos (docs/07): `clean` (limpio), `highlight` (palabra destacada con `\\k`; si el cue no trae
palabras cae a `clean`) y `outline` (contorno grueso).
"""

from dataclasses import dataclass
from typing import Any, Literal

StyleName = Literal["clean", "highlight", "outline"]
STYLES: tuple[StyleName, ...] = ("clean", "highlight", "outline")

# Zona segura 9:16 (docs/07): 6 % lateral, 12 % superior, 20 % inferior.
SAFE_SIDE = 0.06
SAFE_TOP = 0.12
SAFE_BOTTOM = 0.20
PLAIN_MARGIN = 0.05


@dataclass(frozen=True)
class AssStyle:
    font: str
    size_frac: float
    primary: str  # texto ya "cantado" / texto fijo
    secondary: str  # texto aún no cantado (karaoke)
    outline: str
    outline_frac: float
    bold: bool


_STYLE_TABLE: dict[str, AssStyle] = {
    "clean": AssStyle("DejaVu Sans", 0.040, "#FFFFFF", "#FFFFFF", "#000000", 0.0035, True),
    "highlight": AssStyle("DejaVu Sans", 0.042, "#FFE600", "#FFFFFF", "#000000", 0.0040, True),
    "outline": AssStyle("DejaVu Sans", 0.042, "#FFFFFF", "#FFFFFF", "#000000", 0.0080, True),
}


def _bgr(hex_color: str) -> str:
    """`#RRGGBB` → `&H00BBGGRR` (ASS usa BGR y alfa 00 = opaco)."""
    h = hex_color.lstrip("#")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def _stamp(ms: int) -> str:
    cs = round(ms / 10)
    h, rest = divmod(cs, 360_000)
    m, rest = divmod(rest, 6_000)
    s, c = divmod(rest, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def escape_text(text: str) -> str:
    """Neutraliza todo lo que ASS interpretaría como comando."""
    safe = text.replace("\\", "/").replace("{", "(").replace("}", ")")
    return "\\N".join(line for line in safe.replace("\r", "").split("\n"))


def _karaoke(words: list[dict[str, Any]], cue_start: int, cue_end: int) -> str:
    out: list[str] = []
    cursor = cue_start
    for i, w in enumerate(words):
        gap = max(0, int(w["start_ms"]) - cursor)
        dur = max(0, int(w["end_ms"]) - int(w["start_ms"]))
        # el hueco anterior se suma a la palabra previa para no desfasar el resaltado
        if gap:
            out.append(f"{{\\k{round(gap / 10)}}}")
        out.append(f"{{\\k{max(1, round(dur / 10))}}}{escape_text(str(w['text']))}")
        if i < len(words) - 1:
            out.append(" ")
        cursor = int(w["end_ms"])
    tail = cue_end - cursor
    if tail > 0:
        out.append(f"{{\\k{round(tail / 10)}}}")
    return "".join(out)


def build_ass(
    cues: list[dict[str, Any]],
    *,
    width: int,
    height: int,
    style: str = "clean",
    safe_area: str | None = "shorts",
    duration_ms: int | None = None,
) -> str:
    """ASS completo. `cues` son los de la instantánea de la versión (ya validados)."""
    st = _STYLE_TABLE.get(style, _STYLE_TABLE["clean"])
    size = max(10, round(height * st.size_frac))
    outline = max(1, round(height * st.outline_frac))
    side = round(width * (SAFE_SIDE if safe_area == "shorts" else PLAIN_MARGIN))
    bottom = round(height * (SAFE_BOTTOM if safe_area == "shorts" else PLAIN_MARGIN))
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{st.font},{size},{_bgr(st.primary)},{_bgr(st.secondary)},"
        f"{_bgr(st.outline)},&H00000000,{-1 if st.bold else 0},0,0,0,100,100,0,0,1,"
        f"{outline},0,2,{side},{side},{bottom},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for cue in cues:
        start, end = int(cue["start_ms"]), int(cue["end_ms"])
        if duration_ms is not None:
            if start >= duration_ms:
                continue  # empieza después del final del clip: no se ve
            end = min(end, duration_ms)
        words = cue.get("words")
        if style == "highlight" and words:
            text = _karaoke(words, start, end)
        else:
            text = escape_text(str(cue["text"]))
        lines.append(f"Dialogue: 0,{_stamp(start)},{_stamp(end)},Default,,0,0,0,,{text}")
    return "\n".join(lines) + "\n"
