from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from importlib import import_module
from typing import Protocol, cast

from app.config import get_settings

ALGORITHM = "HS256"


class _JWTModule(Protocol):
    def encode(self, claims: Mapping[str, object], key: str, algorithm: str) -> str: ...

    def decode(self, token: str, key: str, algorithms: list[str]) -> dict[str, object]: ...


class _BcryptHasher(Protocol):
    def verify(self, secret: str, hash: str) -> bool: ...


jwt = cast(_JWTModule, cast(object, import_module("jose.jwt")))
bcrypt = cast(_BcryptHasher, getattr(import_module("passlib.hash"), "bcrypt"))


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.verify(plain_password, hashed_password)


def create_access_token(data: dict[str, object], expires_delta: timedelta | None = None) -> str:
    settings = get_settings()
    expire = datetime.now(timezone.utc) + (
        expires_delta if expires_delta is not None else timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    )
    to_encode = data.copy()
    to_encode["exp"] = expire
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, object]:
    settings = get_settings()
    return dict(jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM]))
