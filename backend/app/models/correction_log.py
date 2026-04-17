from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:  # pragma: no cover
    from app.models.invoice import Invoice


def utcnow() -> datetime:  # pragma: no cover
    return datetime.now(timezone.utc)


class CorrectionLog(Base):
    __tablename__: ClassVar[str] = "correction_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    field_name: Mapped[str] = mapped_column(String(64))
    old_value: Mapped[str | None] = mapped_column(Text(), nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text(), nullable=True)
    corrected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    invoice: Mapped["Invoice"] = relationship(back_populates="correction_logs")
