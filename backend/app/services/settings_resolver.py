from __future__ import annotations

import time
from typing import Literal, TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import AppSettings
from app.services.email_scanner import decrypt_password

AI_SETTINGS_TTL_SECONDS = 60.0
AI_SETTINGS_KEY_MAP = {
    "llm_base_url": "LLM_BASE_URL",
    "llm_api_key": "LLM_API_KEY",
    "llm_model": "LLM_MODEL",
    "llm_embed_model": "LLM_EMBED_MODEL",
    "embed_dim": "EMBED_DIM",
}
AI_SETTINGS_DB_KEYS = tuple(AI_SETTINGS_KEY_MAP)
AI_SEEDED_LLM_DB_KEYS = tuple(key for key in AI_SETTINGS_DB_KEYS if key.startswith("llm_"))
AI_SETTINGS_ENV_TO_DB_KEY = {env_key: db_key for db_key, env_key in AI_SETTINGS_KEY_MAP.items()}


class ResolvedAISettings(TypedDict):
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_embed_model: str
    embed_dim: int
    source: Literal["database", "environment"]


_ai_settings_cache: ResolvedAISettings | None = None
_ai_settings_cache_deadline = 0.0


def invalidate_ai_settings_cache() -> None:
    global _ai_settings_cache, _ai_settings_cache_deadline
    _ai_settings_cache = None
    _ai_settings_cache_deadline = 0.0


def _env_ai_settings(settings: Settings) -> ResolvedAISettings:
    return {
        "llm_base_url": settings.LLM_BASE_URL,
        "llm_api_key": settings.LLM_API_KEY,
        "llm_model": settings.LLM_MODEL,
        "llm_embed_model": settings.LLM_EMBED_MODEL,
        "embed_dim": settings.EMBED_DIM,
        "source": "environment",
    }


async def resolve_ai_settings(db: AsyncSession) -> ResolvedAISettings:
    global _ai_settings_cache, _ai_settings_cache_deadline

    now = time.monotonic()
    if _ai_settings_cache is not None and now < _ai_settings_cache_deadline:
        return _ai_settings_cache.copy()

    settings = get_settings()
    resolved = _env_ai_settings(settings)
    result = await db.execute(select(AppSettings).where(AppSettings.key.in_(AI_SETTINGS_DB_KEYS)))
    rows = {row.key: row.value for row in result.scalars().all()}

    if rows:
        resolved["source"] = "database"
        if "llm_base_url" in rows:
            resolved["llm_base_url"] = rows["llm_base_url"]
        if "llm_api_key" in rows:
            resolved["llm_api_key"] = decrypt_password(rows["llm_api_key"], settings.JWT_SECRET)
        if "llm_model" in rows:
            resolved["llm_model"] = rows["llm_model"]
        if "llm_embed_model" in rows:
            resolved["llm_embed_model"] = rows["llm_embed_model"]
        if "embed_dim" in rows:
            resolved["embed_dim"] = int(rows["embed_dim"])

    _ai_settings_cache = resolved.copy()
    _ai_settings_cache_deadline = now + AI_SETTINGS_TTL_SECONDS
    return resolved


class SettingsResolver:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(self, key: str) -> str | int:
        resolved = await resolve_ai_settings(self._db)
        db_key = AI_SETTINGS_ENV_TO_DB_KEY.get(key)
        if db_key is None:
            settings = get_settings()
            return getattr(settings, key)
        return resolved[db_key]
