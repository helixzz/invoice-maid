from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LLMCache(Base):
    __tablename__: ClassVar[str] = "llm_cache"
    __table_args__: ClassVar[tuple] = (
        Index("ix_llm_cache_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    prompt_type: Mapped[str] = mapped_column(String(32))
    response_json: Mapped[str] = mapped_column(Text())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
