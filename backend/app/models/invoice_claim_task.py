from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import ClassVar

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def utcnow() -> datetime:  # pragma: no cover
    return datetime.now(timezone.utc)


class InvoiceClaimTask(Base):
    """Lifecycle state machine for QR-code / offline receipt invoice claiming."""

    __tablename__: ClassVar[str] = "invoice_claim_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoice_profiles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_channel: Mapped[str] = mapped_column(String(32), nullable=False, default="web")
    source_chat_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    qr_url: Mapped[str] = mapped_column(Text(), nullable=False, default="")
    merchant_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    expected_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    expected_currency: Mapped[str] = mapped_column(String(8), nullable=False, default="CNY")
    receipt_date: Mapped[date | None] = mapped_column(Date(), nullable=True)
    receipt_summary: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="awaiting_profile", index=True
    )
    attention_reason: Mapped[str] = mapped_column(Text(), nullable=False, default="")
    last_screenshot_path: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    matched_invoice_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    log_json: Mapped[str] = mapped_column(Text(), nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
