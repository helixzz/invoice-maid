from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.invoice import Invoice
    from app.models.scan_log import ScanLog


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EmailAccount(Base):
    __tablename__: ClassVar[str] = "email_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    type: Mapped[str] = mapped_column(String(32))
    host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    port: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    username: Mapped[str] = mapped_column(String(255))
    outlook_account_type: Mapped[str] = mapped_column(String(16), default="personal")
    password_encrypted: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    oauth_token_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean(), default=True)
    last_scan_uid: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    invoices: Mapped[list["Invoice"]] = relationship(back_populates="email_account", cascade="all, delete-orphan")
    scan_logs: Mapped[list["ScanLog"]] = relationship(back_populates="email_account", cascade="all, delete-orphan")
