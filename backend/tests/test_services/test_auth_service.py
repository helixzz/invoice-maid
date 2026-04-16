from __future__ import annotations

from datetime import timedelta

from types import SimpleNamespace

import pytest

import app.services.auth_service as auth_service
from app.services.auth_service import create_access_token, decode_access_token, verify_password


def test_verify_password_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        auth_service,
        "bcrypt",
        SimpleNamespace(verify=lambda plain, hashed: hashed == f"hashed:{plain}"),
    )

    assert verify_password("secret", "hashed:secret") is True
    assert verify_password("wrong", "hashed:secret") is False


def test_create_and_decode_access_token_default_expiry(settings) -> None:
    del settings
    token = create_access_token({"sub": "admin", "scope": "full"})
    payload = decode_access_token(token)

    assert payload["sub"] == "admin"
    assert payload["scope"] == "full"
    assert "exp" in payload


def test_create_access_token_custom_expiry(settings) -> None:
    del settings
    token = create_access_token({"sub": "admin"}, expires_delta=timedelta(minutes=1))
    payload = decode_access_token(token)

    assert payload["sub"] == "admin"
