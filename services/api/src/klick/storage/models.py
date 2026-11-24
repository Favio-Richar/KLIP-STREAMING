"""Modelo `storage_objects` (docs/04 §3.3)."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, LargeBinary, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from klick.core.models import Base, IdMixin, TimestampMixin


class StorageObject(IdMixin, TimestampMixin, Base):
    __tablename__ = "storage_objects"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    backend: Mapped[str] = mapped_column(Text, nullable=False, server_default="s3")
    bucket: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[bytes | None] = mapped_column(LargeBinary)
    mime: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="uploading")
    ref_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    orphaned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
