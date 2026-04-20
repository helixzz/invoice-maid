from __future__ import annotations

from importlib import import_module
from typing import Protocol, TypeVar, cast

from fastapi import Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.deps import CurrentUserAndSession
from app.models import User
from app.rate_limiter import limiter
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LogoutResponse,
    RegisterRequest,
    SessionSummary,
    TokenResponse,
    UserInfo,
)
from app.services.auth_service import (
    create_access_token,
    create_user_session,
    hash_password,
    revoke_all_sessions,
    revoke_session,
    verify_password,
)

_F = TypeVar("_F")


class _RouteDecorator(Protocol):
    def __call__(self, func: _F, /) -> _F: ...


class _RouterProtocol(Protocol):
    def post(self, path: str, *, response_model: type[object]) -> _RouteDecorator: ...

    def get(self, path: str, *, response_model: type[object]) -> _RouteDecorator: ...


class _RouterFactory(Protocol):
    def __call__(self, *, prefix: str = "", tags: list[str] | None = None) -> _RouterProtocol: ...


class _StatusProtocol(Protocol):
    HTTP_400_BAD_REQUEST: int
    HTTP_401_UNAUTHORIZED: int
    HTTP_403_FORBIDDEN: int
    HTTP_409_CONFLICT: int
    HTTP_422_UNPROCESSABLE_ENTITY: int


class _HTTPExceptionFactory(Protocol):
    def __call__(self, *, status_code: int, detail: str) -> Exception: ...


_fastapi = import_module("fastapi")

APIRouter = cast(_RouterFactory, getattr(_fastapi, "APIRouter"))
HTTPException = cast(_HTTPExceptionFactory, getattr(_fastapi, "HTTPException"))
status = cast(_StatusProtocol, getattr(_fastapi, "status"))

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str | None:
    client = getattr(request, "client", None)
    if client is not None and getattr(client, "host", None):
        return cast(str, client.host)
    return None


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    del response
    settings = get_settings()

    if payload.email is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="email is required",
        )

    email = str(payload.email).strip().lower()
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active or not verify_password(
        payload.password, user.hashed_password
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    token = create_access_token({"sub": str(user.id)})
    user_agent = request.headers.get("user-agent")
    await create_user_session(
        db,
        user,
        token,
        user_agent=user_agent[:500] if user_agent else None,
        ip_address=_client_ip(request),
        settings=settings,
    )
    return TokenResponse(access_token=token)


@router.post("/register", response_model=TokenResponse)
@limiter.limit("5/minute")
async def register(
    request: Request,
    response: Response,
    payload: RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Create a new non-admin user and log them in.

    Gated by ``ALLOW_REGISTRATION`` env var (default false). When
    disabled, returns 403 with a generic message — the endpoint's
    existence is still visible to unauthenticated callers, but no
    information about the instance's user database is leaked."""
    del response
    settings = get_settings()

    if not settings.ALLOW_REGISTRATION:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration is disabled on this instance",
        )

    email = payload.email.strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="email must contain '@'",
        )

    if payload.password != payload.password_confirm:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="password and password_confirm do not match",
        )

    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email is already registered",
        )

    user = User(
        email=email,
        hashed_password=hash_password(payload.password),
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = create_access_token({"sub": str(user.id)})
    user_agent = request.headers.get("user-agent")
    await create_user_session(
        db,
        user,
        token,
        user_agent=user_agent[:500] if user_agent else None,
        ip_address=_client_ip(request),
        settings=settings,
    )
    return TokenResponse(access_token=token)


@router.put("/me/password", response_model=LogoutResponse)
async def change_password(
    resolved: CurrentUserAndSession,
    payload: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
) -> LogoutResponse:
    """Change the caller's password.

    Verifies the current password server-side, hashes the new one,
    then revokes every OTHER session for the user so stolen tokens on
    other devices stop working immediately. The caller's current
    session stays valid — no forced re-login on this device."""
    user, session = resolved

    if not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    if payload.new_password != payload.new_password_confirm:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="new_password and new_password_confirm do not match",
        )

    if payload.new_password == payload.current_password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="new_password must differ from current_password",
        )

    user.hashed_password = hash_password(payload.new_password)
    await db.commit()

    from app.models import UserSession

    result = await db.execute(
        select(UserSession).where(
            UserSession.user_id == user.id,
            UserSession.revoked_at.is_(None),
            UserSession.id != session.id,
        )
    )
    for other in result.scalars().all():
        await revoke_session(db, other)

    return LogoutResponse(success=True)


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    resolved: CurrentUserAndSession,
    db: AsyncSession = Depends(get_db),
) -> LogoutResponse:
    _, session = resolved
    await revoke_session(db, session)
    return LogoutResponse(success=True)


@router.post("/logout-all", response_model=LogoutResponse)
async def logout_all(
    resolved: CurrentUserAndSession,
    db: AsyncSession = Depends(get_db),
) -> LogoutResponse:
    user, _ = resolved
    await revoke_all_sessions(db, user.id)
    return LogoutResponse(success=True)


@router.get("/me", response_model=UserInfo)
async def me(resolved: CurrentUserAndSession) -> UserInfo:
    user, _ = resolved
    return UserInfo(
        id=user.id,
        email=user.email,
        is_active=user.is_active,
        is_admin=user.is_admin,
        created_at=user.created_at,
    )


@router.get("/sessions", response_model=list[SessionSummary])
async def list_sessions(
    resolved: CurrentUserAndSession,
    db: AsyncSession = Depends(get_db),
) -> list[SessionSummary]:
    from app.models import UserSession

    user, _ = resolved
    result = await db.execute(
        select(UserSession)
        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        .order_by(UserSession.last_seen_at.desc())
    )
    return [
        SessionSummary(
            id=s.id,
            created_at=s.created_at,
            expires_at=s.expires_at,
            last_seen_at=s.last_seen_at,
            user_agent=s.user_agent,
            ip_address=s.ip_address,
        )
        for s in result.scalars().all()
    ]
