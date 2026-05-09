from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:  # pragma: no cover
    from app.models.correction_log import CorrectionLog
    from app.models.email_account import EmailAccount


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Invoice(Base):
    __tablename__: ClassVar[str] = "invoices"
    __table_args__ = (
        UniqueConstraint("user_id", "invoice_no", name="uq_invoices_user_id_invoice_no"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    invoice_no: Mapped[str] = mapped_column(String(128), index=True)
    buyer: Mapped[str] = mapped_column(String(255))
    seller: Mapped[str] = mapped_column(String(255))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    invoice_date: Mapped[date] = mapped_column(Date(), index=True)
    invoice_type: Mapped[str] = mapped_column(String(128))
    invoice_category: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="vat_invoice", index=True
    )
    item_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    file_path: Mapped[str] = mapped_column(String(500))
    raw_text: Mapped[str] = mapped_column(Text(), default="")
    email_uid: Mapped[str] = mapped_column(String(255))
    email_account_id: Mapped[int] = mapped_column(ForeignKey("email_accounts.id", ondelete="CASCADE"), index=True)
    source_format: Mapped[str] = mapped_column(String(32), default="pdf")
    extraction_method: Mapped[str] = mapped_column(String(32), default="llm")
    confidence: Mapped[float] = mapped_column(default=0.0)
    is_manually_corrected: Mapped[bool] = mapped_column(Boolean(), default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    email_account: Mapped[EmailAccount] = relationship(back_populates="invoices")
    correction_logs: Mapped[list["CorrectionLog"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )
