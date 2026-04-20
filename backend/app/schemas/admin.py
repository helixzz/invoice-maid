from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AdminUserSummary(BaseModel):
    id: int
    email: str
    is_active: bool
    is_admin: bool
    created_at: datetime
    invoice_count: int


class AdminUserPatch(BaseModel):
    is_active: bool | None = None
    is_admin: bool | None = None
    email: str | None = Field(default=None, min_length=3, max_length=255)
