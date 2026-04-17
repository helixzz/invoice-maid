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
    JWT_SECRET: str
    LLM_BASE_URL: str
    LLM_API_KEY: str
    STORAGE_PATH: str = "./data/invoices"
    JWT_EXPIRE_MINUTES: int = 1440
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_EMBED_MODEL: str = "text-embedding-3-small"
    EMBED_DIM: int = 1536
    SCAN_INTERVAL_MINUTES: int = 60
    SQLITE_VEC_ENABLED: bool = True
    WEBHOOK_URL: str = ""
    WEBHOOK_SECRET: str = ""
    ENABLE_TEST_HELPERS: bool = False
    LOG_LEVEL: str = "INFO"
    sqlite_vec_available: bool = Field(default=False, validation_alias="__runtime_sqlite_vec_available__")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
