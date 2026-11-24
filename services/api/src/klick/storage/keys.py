"""Claves de objeto (ADR-005). Todas llevan el ID de la organización como primer segmento."""

import re
import uuid

_MAX_KEY_LEN = 1024
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._=@+-]+$")
DERIVATIVE_KINDS = ("proxy_720p", "thumbnail", "sprite", "waveform", "audio_16k")


class InvalidKeyError(ValueError):
    pass


def validate_key(key: str) -> str:
    """Rechaza claves que puedan escapar de su prefijo o confundir al almacén."""
    if not key or len(key) > _MAX_KEY_LEN or key.startswith("/") or key.endswith("/"):
        raise InvalidKeyError("clave vacía, demasiado larga o con barras en los extremos")
    for segment in key.split("/"):
        if segment in ("", ".", "..") or not _SAFE_SEGMENT.match(segment):
            raise InvalidKeyError(f"segmento no permitido: {segment!r}")
    return key


def org_prefix(org_id: uuid.UUID) -> str:
    return f"org/{org_id}/"


def asset_original(org_id: uuid.UUID, asset_id: uuid.UUID) -> str:
    return f"org/{org_id}/assets/{asset_id}/original"


def asset_derivative(org_id: uuid.UUID, asset_id: uuid.UUID, kind: str) -> str:
    if kind not in DERIVATIVE_KINDS:
        raise InvalidKeyError(f"derivado desconocido: {kind}")
    return f"org/{org_id}/assets/{asset_id}/derivatives/{kind}"


def render_output(org_id: uuid.UUID, render_job_id: uuid.UUID) -> str:
    return f"org/{org_id}/renders/{render_job_id}.mp4"


def live_segment(org_id: uuid.UUID, session_id: uuid.UUID, seq: int) -> str:
    if seq < 0:
        raise InvalidKeyError("seq negativo")
    return f"org/{org_id}/live/{session_id}/seg_{seq}.m4s"


def belongs_to_org(key: str, org_id: uuid.UUID) -> bool:
    return key.startswith(org_prefix(org_id))
