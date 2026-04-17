from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import CurrentUser
from app.models import AppSettings

router = APIRouter(prefix="/settings/classifier", tags=["settings"])

_KEYS = ("classifier_trusted_senders", "classifier_extra_keywords")


class ClassifierSettingsResponse(BaseModel):
    trusted_senders: str
    extra_keywords: str


class ClassifierSettingsUpdate(BaseModel):
    trusted_senders: str | None = None
    extra_keywords: str | None = None


async def _get_value(db: AsyncSession, key: str) -> str:
    result = await db.execute(select(AppSettings.value).where(AppSettings.key == key))
    return result.scalar_one_or_none() or ""


async def _set_value(db: AsyncSession, key: str, value: str) -> None:
    row = await db.get(AppSettings, key)
    if row is None:
        db.add(AppSettings(key=key, value=value))
    else:
        row.value = value


@router.get("", response_model=ClassifierSettingsResponse)
async def get_classifier_settings(
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> ClassifierSettingsResponse:
    return ClassifierSettingsResponse(
        trusted_senders=await _get_value(db, "classifier_trusted_senders"),
        extra_keywords=await _get_value(db, "classifier_extra_keywords"),
    )


@router.put("", response_model=ClassifierSettingsResponse)
async def update_classifier_settings(
    payload: ClassifierSettingsUpdate,
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> ClassifierSettingsResponse:
    if payload.trusted_senders is not None:
        await _set_value(db, "classifier_trusted_senders", payload.trusted_senders)
    if payload.extra_keywords is not None:
        await _set_value(db, "classifier_extra_keywords", payload.extra_keywords)
    await db.commit()
    return ClassifierSettingsResponse(
        trusted_senders=await _get_value(db, "classifier_trusted_senders"),
        extra_keywords=await _get_value(db, "classifier_extra_keywords"),
    )
