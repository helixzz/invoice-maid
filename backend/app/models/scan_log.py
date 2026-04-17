from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:  # pragma: no cover
    from app.models.email_account import EmailAccount
    from app.models.extraction_log import ExtractionLog


def utcnow() -> datetime:  # pragma: no cover
    return datetime.now(timezone.utc)


class ScanLog(Base):
    __tablename__: ClassVar[str] = "scan_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    email_account_id: Mapped[int] = mapped_column(ForeignKey("email_accounts.id", ondelete="CASCADE"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    emails_scanned: Mapped[int] = mapped_column(default=0)
    invoices_found: Mapped[int] = mapped_column(default=0)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    email_account: Mapped[EmailAccount] = relationship(back_populates="scan_logs")
    extraction_logs: Mapped[list["ExtractionLog"]] = relationship(
        back_populates="scan_log", cascade="all, delete-orphan"
    )
