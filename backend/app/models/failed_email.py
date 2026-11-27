from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def utcnow() -> datetime:  # pragma: no cover
    return datetime.now(timezone.utc)


class FailedEmail(Base):
    """Persistent retry queue (DLQ) for emails whose processing failed due to transient
    infrastructure issues (e.g. LLM outage, circuit breaker open, Outlook 429 hydrate failure)."""

    __tablename__: ClassVar[str] = "failed_emails"
    __table_args__ = (
        UniqueConstraint(
            "email_account_id",
            "email_uid",
            name="uq_failed_emails_account_uid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email_account_id: Mapped[int] = mapped_column(
        ForeignKey("email_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email_uid: Mapped[str] = mapped_column(String(255), nullable=False)
    email_subject: Mapped[str] = mapped_column(String(500), default="")
    from_addr: Mapped[str] = mapped_column(String(500), default="")
    body_text: Mapped[str] = mapped_column(Text(), default="")
    body_links_json: Mapped[str] = mapped_column(Text(), default="[]")
    headers_json: Mapped[str] = mapped_column(Text(), default="{}")
    is_hydrated: Mapped[bool] = mapped_column(Boolean(), default=False)
    error_message: Mapped[str] = mapped_column(String(2000), default="")
    retry_count: Mapped[int] = mapped_column(Integer(), default=0)
    max_retries: Mapped[int] = mapped_column(Integer(), default=5)
    next_retry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
