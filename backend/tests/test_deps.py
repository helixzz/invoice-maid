from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from jose import ExpiredSignatureError, JWTError

import app.deps as deps
from app.services.auth_service import create_access_token


def test_get_current_user_requires_token() -> None:
    with pytest.raises(HTTPException, match="Not authenticated") as exc_info:
        deps.get_current_user(None)

    assert exc_info.value.status_code == 401


def test_get_current_user_rejects_expired_token() -> None:
    token = create_access_token({"sub": "admin"}, expires_delta=timedelta(seconds=-1))
    credentials = SimpleNamespace(credentials=token)

    with pytest.raises(HTTPException, match="Token has expired") as exc_info:
        deps.get_current_user(credentials)

    assert exc_info.value.status_code == 401


def test_get_current_user_rejects_invalid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "decode_access_token", lambda token: (_ for _ in ()).throw(JWTError("bad")))

    with pytest.raises(HTTPException, match="Invalid authentication credentials"):
        deps.get_current_user(SimpleNamespace(credentials="bad-token"))


def test_get_current_user_rejects_missing_subject(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "decode_access_token", lambda token: {"sub": ""})

    with pytest.raises(HTTPException, match="Invalid authentication credentials"):
        deps.get_current_user(SimpleNamespace(credentials="token"))


def test_get_current_user_returns_subject() -> None:
    token = create_access_token({"sub": "admin"})
    credentials = SimpleNamespace(credentials=token)

    assert deps.get_current_user(credentials) == "admin"


def test_expired_signature_error_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        deps,
        "decode_access_token",
        lambda token: (_ for _ in ()).throw(ExpiredSignatureError("expired")),
    )

    with pytest.raises(HTTPException, match="Token has expired"):
        deps.get_current_user(SimpleNamespace(credentials="token"))
