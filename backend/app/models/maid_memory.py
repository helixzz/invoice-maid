from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def utcnow() -> datetime:  # pragma: no cover
    return datetime.now(timezone.utc)


class MaidMemory(Base):
    """Long-term user preferences and deterministic automation rules learned by Invoice Maid."""

    __tablename__: ClassVar[str] = "maid_memories"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="general")
    rule_text: Mapped[str] = mapped_column(String(500), nullable=False)
    match_pattern: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    action_json: Mapped[str] = mapped_column(Text(), nullable=False, default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
