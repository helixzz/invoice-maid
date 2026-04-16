from __future__ import annotations

from app.config import Settings, get_settings


def test_settings_reads_required_values(settings: Settings) -> None:
    assert settings.JWT_SECRET == "test-secret"
    assert settings.EMBED_DIM == 3


def test_get_settings_uses_cache(settings: Settings) -> None:
    get_settings.cache_clear()
    first = get_settings()
    second = get_settings()
    assert first is second
    assert first.JWT_SECRET == settings.JWT_SECRET
