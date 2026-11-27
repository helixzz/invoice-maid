from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def utcnow() -> datetime:  # pragma: no cover
    return datetime.now(timezone.utc)


class MaidMessage(Base):
    """Persistent conversation turn history for the IM-agnostic MaidAgentRunner."""

    __tablename__: ClassVar[str] = "maid_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default", index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text(), nullable=False, default="")
    tool_calls_json: Mapped[str] = mapped_column(Text(), nullable=False, default="[]")
    attachments_json: Mapped[str] = mapped_column(Text(), nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
