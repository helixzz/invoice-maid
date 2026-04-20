from __future__ import annotations

import hashlib
import secrets
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from importlib import import_module
from typing import Protocol, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import User, UserSession

ALGORITHM = "HS256"


class _JWTModule(Protocol):
    def encode(self, claims: Mapping[str, object], key: str, algorithm: str) -> str: ...

    def decode(self, token: str, key: str, algorithms: list[str]) -> dict[str, object]: ...


class _BcryptHasher(Protocol):
    def verify(self, secret: str, hash: str) -> bool: ...

    def hash(self, secret: str) -> str: ...


jwt = cast(_JWTModule, cast(object, import_module("jose.jwt")))
bcrypt = cast(_BcryptHasher, getattr(import_module("passlib.hash"), "bcrypt"))


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.verify(plain_password, hashed_password)


def hash_password(plain_password: str) -> str:
    """Bcrypt-hash a password for storage in ``users.hashed_password``.

    Same algorithm the bootstrap admin uses via ``ADMIN_PASSWORD_HASH``.
    The test harness stubs ``bcrypt.hash`` with a deterministic
    ``hashed:{plain}`` fake so fixtures don't pay the bcrypt cost on
    every test setup — see ``tests/conftest.py``."""
    return bcrypt.hash(plain_password)


def create_access_token(data: dict[str, object], expires_delta: timedelta | None = None) -> str:
    settings = get_settings()
    expire = datetime.now(timezone.utc) + (
        expires_delta if expires_delta is not None else timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    )
    to_encode = data.copy()
    to_encode["exp"] = expire
    to_encode.setdefault("jti", secrets.token_hex(8))
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, object]:
    settings = get_settings()
    return dict(jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM]))


def hash_token(token: str) -> str:
    """Per-session fingerprint of a raw JWT. Stored in ``user_sessions.token_hash``
    so the server never holds the raw token but can still look up and revoke
    the session when a request presents the token. SHA-256 is sufficient here
    because the input already has ~128 bits of entropy from the JWT signature."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_user_session(
    db: AsyncSession,
    user: User,
    token: str,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
    settings: Settings | None = None,
) -> UserSession:
    settings = settings or get_settings()
    now = datetime.now(timezone.utc)
    session = UserSession(
        user_id=user.id,
        token_hash=hash_token(token),
        created_at=now,
        expires_at=now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
        last_seen_at=now,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def resolve_active_session(
    db: AsyncSession, token: str
) -> tuple[User, UserSession] | None:
    """Look up the session row for a raw JWT and return (user, session) only
    if the session is both unrevoked and its user is active. Returns ``None``
    for any failure condition — the caller converts that into 401 so all
    failure modes look identical to a probe."""
    token_hash = hash_token(token)
    result = await db.execute(
        select(UserSession).where(UserSession.token_hash == token_hash)
    )
    session = result.scalar_one_or_none()
    if session is None or session.revoked_at is not None:
        return None
    now = datetime.now(timezone.utc)
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        return None

    user = await db.get(User, session.user_id)
    if user is None or not user.is_active:
        return None

    session.last_seen_at = now
    await db.commit()
    return user, session


async def revoke_session(db: AsyncSession, session: UserSession) -> None:
    session.revoked_at = datetime.now(timezone.utc)
    await db.commit()


async def revoke_all_sessions(db: AsyncSession, user_id: int) -> int:
    result = await db.execute(
        select(UserSession).where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
        )
    )
    sessions = list(result.scalars().all())
    now = datetime.now(timezone.utc)
    for session in sessions:
        session.revoked_at = now
    await db.commit()
    return len(sessions)
