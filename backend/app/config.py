from functools import lru_cache
from typing import TYPE_CHECKING, ClassVar, TypedDict

from pydantic import Field

if TYPE_CHECKING:
    class SettingsConfigDict(TypedDict, total=False):
        env_file: str
        env_file_encoding: str


    class BaseSettings:
        model_config: ClassVar[SettingsConfigDict]
else:
    from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    DATABASE_URL: str
    ADMIN_PASSWORD_HASH: str
    ADMIN_EMAIL: str = "admin@local"
    JWT_SECRET: str
    LLM_BASE_URL: str
    LLM_API_KEY: str
    STORAGE_PATH: str = "./data/invoices"
    JWT_EXPIRE_MINUTES: int = 1440
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_FALLBACK_MODELS: str = ""
    LLM_VISION_MODEL: str = ""
    LLM_EMBED_BASE_URL: str = ""
    LLM_EMBED_API_KEY: str = ""
    LLM_EMBED_MODEL: str = "text-embedding-3-small"
    EMBED_DIM: int = 1536
    SCAN_INTERVAL_MINUTES: int = 60
    SQLITE_VEC_ENABLED: bool = True
    WEBHOOK_URL: str = ""
    WEBHOOK_SECRET: str = ""
    TELEGRAM_ENABLED: bool = False
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    TELEGRAM_NOTIFY_ON_ERROR: bool = True
    TELEGRAM_NOTIFY_ON_SCAN_SUMMARY: bool = True
    TELEGRAM_AGENT_ENABLED: bool = False
    TELEGRAM_AGENT_BOT_TOKEN: str = ""
    TELEGRAM_AGENT_ALLOWED_CHAT_IDS: str = ""
    MAID_HEARTBEAT_MINUTES: int = 10
    ENABLE_TEST_HELPERS: bool = False
    ALLOW_REGISTRATION: bool = False
    OUTLOOK_DELTA_ENABLED: bool = True
    OUTLOOK_DELTA_FALLBACK_ON_RESYNC: bool = True
    STRICT_VAT_ONLY: bool = False
    LOG_LEVEL: str = "INFO"
    OUTLOOK_PERSONAL_CLIENT_ID: str = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
    # Microsoft Graph Command Line Tools — the Office first-party id
    # (d3590ed6-...) is no longer preauthorized for Graph scopes on
    # work/school tenants and fails with AADSTS65002.
    OUTLOOK_AAD_CLIENT_ID: str = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
    sqlite_vec_available: bool = Field(default=False, validation_alias="__runtime_sqlite_vec_available__")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
