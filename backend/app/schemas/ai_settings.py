from typing import Annotated

from pydantic import AnyHttpUrl, BaseModel, Field

NonEmptyString = Annotated[str, Field(min_length=1)]


class AISettingsResponse(BaseModel):
    llm_base_url: str
    llm_api_key_masked: str
    llm_model: str
    llm_embed_model: str
    embed_dim: int
    source: str


class AISettingsUpdate(BaseModel):
    llm_base_url: AnyHttpUrl | None = None
    llm_api_key: NonEmptyString | None = None
    llm_model: NonEmptyString | None = None
    llm_embed_model: NonEmptyString | None = None
    embed_dim: Annotated[int, Field(gt=0)] | None = None


class ModelListResponse(BaseModel):
    models: list[str]
