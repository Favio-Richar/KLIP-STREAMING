"""Modelo de pistas de subtítulos. El esquema real lo define la migración 0011."""

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from klick.core.models import Base, IdMixin, TimestampMixin


class CaptionTrack(IdMixin, TimestampMixin, Base):
    __tablename__ = "caption_tracks"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    clip_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    language: Mapped[str] = mapped_column(Text, nullable=False, server_default="es")
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="manual")
    cues: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="ready")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


def snapshot_of(track: CaptionTrack) -> dict[str, Any]:
    """Copia inmutable de lo que el render necesita (se guarda en `clip_versions.captions`)."""
    return {
        "track_id": str(track.id),
        "language": track.language,
        "revision": track.revision,
        "cues": track.cues,
    }
