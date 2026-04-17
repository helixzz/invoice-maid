from __future__ import annotations

from pydantic import BaseModel, Field


class SavedViewCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    filter_json: str = Field(min_length=1)


class SavedViewResponse(BaseModel):
    id: int
    name: str
    filter_json: str
    created_at: str
