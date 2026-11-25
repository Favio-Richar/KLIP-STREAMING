"""Cues de subtítulos: modelo y reglas. Puro, sin base de datos.

Los textos son contenido del usuario (dato no confiable, CLAUDE.md regla 6): aquí se limitan en
tamaño y se rechazan los caracteres de control. El escape para ASS ocurre en `render/ass.py`.

Los tiempos son milisegundos de la LÍNEA DE TIEMPO del clip (no de la fuente), igual que los
elementos de texto del documento de edición.
"""

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

MAX_CUES = 1000
MAX_TEXT_CHARS = 200
MAX_LINES = 2
MAX_LINE_CHARS = 60
MAX_MS = 86_400_000
MAX_WORDS = 40

_CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")  # todo salvo el salto de línea (\n)

Ms = Annotated[int, Field(ge=0, le=MAX_MS)]


class Word(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_ms: Ms
    end_ms: Ms
    text: Annotated[str, StringConstraints(min_length=1, max_length=60)]


class Cue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_ms: Ms
    end_ms: Ms
    text: str
    words: Annotated[list[Word], Field(max_length=MAX_WORDS)] | None = None

    @field_validator("text")
    @classmethod
    def _text(cls, value: str) -> str:
        value = value.replace("\t", " ")  # un tabulador pegado es un espacio, no un error
        text = "\n".join(line.strip() for line in value.replace("\r\n", "\n").split("\n")).strip()
        if not text:
            raise ValueError("empty_text")
        if len(text) > MAX_TEXT_CHARS:
            raise ValueError("text_too_long")
        if _CONTROL.search(text):
            raise ValueError("control_characters")
        return text


class CueIssue(BaseModel):
    code: str
    index: int
    message: str


def validate_cues(cues: list[Cue]) -> list[CueIssue]:
    """Reglas entre cues: orden, solapes, líneas. Cada problema indica su posición."""
    issues: list[CueIssue] = []
    if len(cues) > MAX_CUES:
        issues.append(CueIssue(code="too_many_cues", index=0, message=f"Máximo {MAX_CUES}."))
        return issues
    previous_end = 0
    for i, cue in enumerate(cues):
        if cue.end_ms <= cue.start_ms:
            issues.append(
                CueIssue(code="invalid_range", index=i, message="El fin debe ser posterior.")
            )
        if cue.start_ms < previous_end:
            issues.append(
                CueIssue(
                    code="cues_overlap",
                    index=i,
                    message="Los subtítulos deben estar en orden y no solaparse.",
                )
            )
        previous_end = max(previous_end, cue.end_ms)
        lines = cue.text.split("\n")
        if len(lines) > MAX_LINES:
            issues.append(
                CueIssue(code="too_many_lines", index=i, message=f"Máximo {MAX_LINES} líneas.")
            )
        elif any(len(line) > MAX_LINE_CHARS for line in lines):
            issues.append(
                CueIssue(
                    code="line_too_long",
                    index=i,
                    message=f"Máximo {MAX_LINE_CHARS} caracteres por línea.",
                )
            )
        for word in cue.words or []:
            if (
                word.end_ms <= word.start_ms
                or word.start_ms < cue.start_ms
                or word.end_ms > cue.end_ms
            ):
                issues.append(
                    CueIssue(
                        code="word_out_of_range",
                        index=i,
                        message="Las palabras deben quedar dentro del subtítulo.",
                    )
                )
                break
    return issues
