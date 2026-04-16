from __future__ import annotations

from importlib import import_module
from typing import Annotated
from typing import Protocol, TypeVar, cast

from app.services.auth_service import decode_access_token

_F = TypeVar("_F")


class _DependsFactory(Protocol):
    def __call__(self, dependency: object) -> object: ...


class _HTTPExceptionFactory(Protocol):
    def __call__(self, *, status_code: int, detail: str) -> Exception: ...


class _StatusProtocol(Protocol):
    HTTP_401_UNAUTHORIZED: int


class _BearerFactory(Protocol):
    def __call__(self, *, auto_error: bool = True) -> object: ...


class _CredentialsLike(Protocol):
    credentials: str


_fastapi = import_module("fastapi")
_security = import_module("fastapi.security")
_jose = import_module("jose")

Depends = cast(_DependsFactory, getattr(_fastapi, "Depends"))
HTTPException = cast(_HTTPExceptionFactory, getattr(_fastapi, "HTTPException"))
status = cast(_StatusProtocol, getattr(_fastapi, "status"))
HTTPBearer = cast(_BearerFactory, getattr(_security, "HTTPBearer"))
ExpiredSignatureError = cast(type[Exception], getattr(_jose, "ExpiredSignatureError"))
JWTError = cast(type[Exception], getattr(_jose, "JWTError"))

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Annotated[object | None, Depends(bearer_scheme)],
) -> str:
    token = cast(str | None, getattr(cast(_CredentialsLike | None, credentials), "credentials", None))
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    try:
        payload = decode_access_token(token)
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

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )

    return subject


CurrentUser = Annotated[str, Depends(get_current_user)]
