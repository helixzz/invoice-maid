from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:  # pragma: no cover
    from app.models.scan_log import ScanLog


def utcnow() -> datetime:  # pragma: no cover
    return datetime.now(timezone.utc)


class ExtractionLog(Base):
    __tablename__: ClassVar[str] = "extraction_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_log_id: Mapped[int] = mapped_column(ForeignKey("scan_logs.id", ondelete="CASCADE"), index=True)
    email_uid: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email_subject: Mapped[str] = mapped_column(String(500))
    attachment_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    outcome: Mapped[str] = mapped_column(String(32))
    classification_tier: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    parse_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    parse_format: Mapped[str | None] = mapped_column(String(10), nullable=True)
    download_outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    invoice_no: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float(), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    scan_log: Mapped["ScanLog"] = relationship(back_populates="extraction_logs")
