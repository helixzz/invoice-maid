from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr | None = Field(
        default=None,
        description="Email address. When omitted the request is rejected; "
        "backward-compat callers that only send `password` get a 422.",
    )
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserInfo(BaseModel):
    id: int
    email: str
    is_active: bool
    is_admin: bool
    created_at: datetime


class SessionSummary(BaseModel):
    id: int
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime
    user_agent: str | None = None
    ip_address: str | None = None


class LogoutResponse(BaseModel):
    success: bool = True

