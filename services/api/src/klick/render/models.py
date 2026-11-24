"""Modelos de render (docs/04 §3.6 y §3.13). El esquema real lo define la migración 0010."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from klick.core.models import Base, IdMixin, TimestampMixin

RENDER_STATUSES = ("queued", "running", "succeeded", "failed", "canceled")
FINAL_RENDER_STATUSES = ("succeeded", "failed", "canceled")


class RenderPresetRow(IdMixin, TimestampMixin, Base):
    __tablename__ = "render_presets"

    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE")
    )
    key: Mapped[str] = mapped_column(Text, nullable=False)
    container: Mapped[str] = mapped_column(Text, nullable=False, server_default="mp4")
    video_codec: Mapped[str] = mapped_column(Text, nullable=False, server_default="libx264")
    audio_codec: Mapped[str] = mapped_column(Text, nullable=False, server_default="aac")
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    fps: Mapped[int] = mapped_column(Integer, nullable=False)
    crf: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("20"))
    max_bitrate_kbps: Mapped[int | None] = mapped_column(Integer)
    audio_bitrate_kbps: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("192")
    )
    loudness_target_lufs: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, server_default=text("-14")
    )
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class RenderJob(IdMixin, TimestampMixin, Base):
    __tablename__ = "render_jobs"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    clip_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    clip_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    preset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("render_presets.id", ondelete="RESTRICT"), nullable=False
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    progress: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    output_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ffmpeg_version: Mapped[str | None] = mapped_column(Text)
    ffmpeg_args_hash: Mapped[str | None] = mapped_column(Text)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UsageRecord(IdMixin, Base):
    """Consumo facturable (solo inserción). La clave de idempotencia evita contarlo dos veces."""

    __tablename__ = "usage_records"

    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    metric: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    billing_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="platform")
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    billed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
