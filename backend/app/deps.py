from __future__ import annotations

from typing import Annotated, Protocol

from fastapi import Depends, Header, HTTPException, Query, status
from jose import ExpiredSignatureError, JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import User, UserSession
from app.services.auth_service import decode_access_token, resolve_active_session


class _OwnedResource(Protocol):
    user_id: int


def assert_owned(resource: _OwnedResource | None, user: User) -> _OwnedResource:
    """Guard every ``db.get(...)``-returned tenant-scoped row.

    Raises 404 — not 403 — when the row is missing or belongs to a
    different user. 403 would leak the existence of another user's
    resource through a distinguishable status code; 404 is the correct
    tenant-isolation response and mirrors what the caller would see if
    the row genuinely did not exist."""
    if resource is None or getattr(resource, "user_id", None) != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )
    return resource


async def get_current_user_and_session(
    authorization: Annotated[str | None, Header()] = None,
    token: Annotated[str | None, Query()] = None,
    db: AsyncSession = Depends(get_db),
) -> tuple[User, UserSession]:
    raw_token = token
    if raw_token is None and authorization is not None:
        raw_token = authorization.removeprefix("Bearer ").strip() or None
    if raw_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    try:
        decode_access_token(raw_token)
    except ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        ) from exc
    except (JWTError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        ) from exc

    resolved = await resolve_active_session(db, raw_token)
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )
    return resolved


async def get_current_user(
    resolved: Annotated[
        tuple[User, UserSession], Depends(get_current_user_and_session)
    ],
) -> User:
    return resolved[0]


CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentUserAndSession = Annotated[tuple[User, UserSession], Depends(get_current_user_and_session)]
