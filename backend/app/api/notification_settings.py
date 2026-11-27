from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ai_settings import _mask_api_key
from app.config import get_settings
from app.database import get_db
from app.deps import CurrentUser
from app.models import AppSettings
from app.services.email_scanner import encrypt_password
from app.services.telegram_notifier import (
    resolve_telegram_settings,
    send_telegram_message,
)

router = APIRouter(prefix="/settings/notifications", tags=["settings"])


class NotificationSettingsResponse(BaseModel):
    telegram_enabled: bool
    telegram_bot_token_masked: str
    telegram_chat_id: str
    telegram_notify_on_error: bool
    telegram_notify_on_scan_summary: bool
    source: str


class NotificationSettingsUpdate(BaseModel):
    telegram_enabled: bool | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_notify_on_error: bool | None = None
    telegram_notify_on_scan_summary: bool | None = None


class NotificationTestResponse(BaseModel):
    ok: bool
    detail: str


async def _upsert(db: AsyncSession, key: str, value: str) -> None:
    row = await db.get(AppSettings, key)
    if row is None:
        db.add(AppSettings(key=key, value=value))
    else:
        row.value = value


def _serialize(resolved: dict[str, object]) -> NotificationSettingsResponse:
    return NotificationSettingsResponse(
        telegram_enabled=bool(resolved["enabled"]),
        telegram_bot_token_masked=_mask_api_key(str(resolved["bot_token"])),
        telegram_chat_id=str(resolved["chat_id"]),
        telegram_notify_on_error=bool(resolved["notify_on_error"]),
        telegram_notify_on_scan_summary=bool(resolved["notify_on_scan_summary"]),
        source=str(resolved["source"]),
    )


@router.get("", response_model=NotificationSettingsResponse)
async def get_notification_settings(
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> NotificationSettingsResponse:
    return _serialize(await resolve_telegram_settings(db))


@router.put("", response_model=NotificationSettingsResponse)
async def update_notification_settings(
    payload: NotificationSettingsUpdate,
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> NotificationSettingsResponse:
    settings = get_settings()
    if payload.telegram_enabled is not None:
        await _upsert(db, "telegram_enabled", "true" if payload.telegram_enabled else "false")
    if payload.telegram_bot_token is not None:
        token = payload.telegram_bot_token.strip()
        stored = encrypt_password(token, settings.JWT_SECRET) if token else ""
        await _upsert(db, "telegram_bot_token", stored)
    if payload.telegram_chat_id is not None:
        await _upsert(db, "telegram_chat_id", payload.telegram_chat_id.strip())
    if payload.telegram_notify_on_error is not None:
        await _upsert(
            db, "telegram_notify_on_error", "true" if payload.telegram_notify_on_error else "false"
        )
    if payload.telegram_notify_on_scan_summary is not None:
        await _upsert(
            db,
            "telegram_notify_on_scan_summary",
            "true" if payload.telegram_notify_on_scan_summary else "false",
        )
    await db.commit()
    return _serialize(await resolve_telegram_settings(db))


@router.post("/test", response_model=NotificationTestResponse)
async def test_notification(
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> NotificationTestResponse:
    cfg = await resolve_telegram_settings(db)
    ok, detail = await send_telegram_message(
        cfg["bot_token"],
        cfg["chat_id"],
        "🔔 <b>Invoice Maid 测试通知</b>\nTelegram 通知通道已连通，异常告警与扫描汇总将推送到这里。",
    )
    return NotificationTestResponse(ok=ok, detail=detail)
