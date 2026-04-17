from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings


def test_settings_reads_required_values(settings: Settings) -> None:
    assert settings.JWT_SECRET == "test-secret"
    assert settings.EMBED_DIM == 3


def test_settings_missing_required_field_raises_validation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            ADMIN_PASSWORD_HASH="hashed:testpass",
            JWT_SECRET="test-secret",
            LLM_BASE_URL="https://llm.invalid/v1",
            LLM_API_KEY="test-key",
        )


def test_settings_accept_required_string_values() -> None:
    settings = Settings(
        _env_file=None,
        DATABASE_URL="sqlite+aiosqlite:///./test.db",
        ADMIN_PASSWORD_HASH="hashed:testpass",
        JWT_SECRET="test-secret",
        LLM_BASE_URL="https://llm.invalid/v1",
        LLM_API_KEY="test-key",
    )

    assert settings.DATABASE_URL == "sqlite+aiosqlite:///./test.db"
    assert settings.ADMIN_PASSWORD_HASH == "hashed:testpass"
    assert settings.JWT_SECRET == "test-secret"
    assert settings.LLM_BASE_URL == "https://llm.invalid/v1"
    assert settings.LLM_API_KEY == "test-key"


def test_get_settings_uses_cache(settings: Settings) -> None:
    get_settings.cache_clear()
    first = get_settings()
    second = get_settings()
    assert first is second
    assert first.JWT_SECRET == settings.JWT_SECRET
