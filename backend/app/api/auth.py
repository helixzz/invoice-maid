from __future__ import annotations

from importlib import import_module
from typing import Protocol, TypeVar, cast

from fastapi import Request, Response

from app.config import get_settings
from app.rate_limiter import limiter
from app.schemas.auth import LoginRequest, TokenResponse
from app.services.auth_service import create_access_token, verify_password

_F = TypeVar("_F")


class _RouteDecorator(Protocol):
    def __call__(self, func: _F, /) -> _F: ...


class _RouterProtocol(Protocol):
    def post(self, path: str, *, response_model: type[object]) -> _RouteDecorator: ...


class _RouterFactory(Protocol):
    def __call__(self, *, prefix: str = "", tags: list[str] | None = None) -> _RouterProtocol: ...


class _StatusProtocol(Protocol):
    HTTP_401_UNAUTHORIZED: int


class _HTTPExceptionFactory(Protocol):
    def __call__(self, *, status_code: int, detail: str) -> Exception: ...


_fastapi = import_module("fastapi")

APIRouter = cast(_RouterFactory, getattr(_fastapi, "APIRouter"))
HTTPException = cast(_HTTPExceptionFactory, getattr(_fastapi, "HTTPException"))
status = cast(_StatusProtocol, getattr(_fastapi, "status"))

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(request: Request, response: Response, payload: LoginRequest) -> TokenResponse:
    del request, response
    settings = get_settings()

    if not verify_password(payload.password, settings.ADMIN_PASSWORD_HASH):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password",
        )

    return TokenResponse(access_token=create_access_token({"sub": "admin"}))
