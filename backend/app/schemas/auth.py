from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="User email. Format validation is DB-equality only so "
        "self-hosted deployments can use bare hostnames like 'admin@local'. "
        "Backward-compat callers that only send `password` get a 422.",
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


class RegisterRequest(BaseModel):
    email: str = Field(
        min_length=3,
        max_length=255,
        description="Email address. Minimum format check only ('@' must be "
        "present); same lenient validation as login so self-hosted "
        "deployments can use bare-hostname emails like 'alice@local'.",
    )
    password: str = Field(
        min_length=8,
        max_length=128,
        description="Plain password. Will be bcrypt-hashed server-side.",
    )
    password_confirm: str = Field(
        min_length=8,
        max_length=128,
        description="Must equal ``password``. Server-side re-check in case the "
        "frontend form is bypassed.",
    )


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)
    new_password_confirm: str = Field(min_length=8, max_length=128)

