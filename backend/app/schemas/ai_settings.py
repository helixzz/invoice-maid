from typing import Annotated

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator

NonEmptyString = Annotated[str, Field(min_length=1)]


class AISettingsResponse(BaseModel):
    llm_base_url: str
    llm_api_key_masked: str
    llm_model: str
    llm_fallback_models: str = ""
    llm_vision_model: str = ""
    llm_embed_base_url: str = ""
    llm_embed_api_key_masked: str = ""
    llm_embed_model: str
    embed_dim: int
    source: str


class AISettingsUpdate(BaseModel):
    llm_base_url: AnyHttpUrl | None = None
    llm_api_key: NonEmptyString | None = None
    llm_model: NonEmptyString | None = None
    llm_fallback_models: str | None = None
    llm_vision_model: str | None = None
    llm_embed_base_url: str | None = None
    llm_embed_api_key: str | None = None
    llm_embed_model: str | None = None
    embed_dim: Annotated[int, Field(gt=0)] | None = None

    @field_validator("llm_embed_base_url")
    @classmethod
    def _validate_optional_embed_url(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return value
        if not (value.startswith("http://") or value.startswith("https://")):
            raise ValueError("llm_embed_base_url must be an http:// or https:// URL")
        return value


class ModelListResponse(BaseModel):
    models: list[str]
