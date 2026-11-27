from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ai_settings import _mask_api_key
from app.config import get_settings
from app.database import get_db
from app.deps import CurrentUser
from app.models import AppSettings
from app.services.email_scanner import encrypt_password
from app.services.telegram_agent_bot import (
    has_token_conflict,
    resolve_agent_bot_settings,
)
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
    telegram_agent_enabled: bool = False
    telegram_agent_bot_token_masked: str = ""
    telegram_agent_allowed_chat_ids: str = ""
    telegram_agent_token_conflict: bool = False
    source: str


class NotificationSettingsUpdate(BaseModel):
    telegram_enabled: bool | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_notify_on_error: bool | None = None
    telegram_notify_on_scan_summary: bool | None = None
    telegram_agent_enabled: bool | None = None
    telegram_agent_bot_token: str | None = None
    telegram_agent_allowed_chat_ids: str | None = None


class NotificationTestResponse(BaseModel):
    ok: bool
    detail: str


async def _upsert(db: AsyncSession, key: str, value: str) -> None:
    row = await db.get(AppSettings, key)
    if row is None:
        db.add(AppSettings(key=key, value=value))
    else:
        row.value = value


async def _build_response(db: AsyncSession) -> NotificationSettingsResponse:
    resolved = await resolve_telegram_settings(db)
    agent_cfg = await resolve_agent_bot_settings(db)
    return NotificationSettingsResponse(
        telegram_enabled=bool(resolved["enabled"]),
        telegram_bot_token_masked=_mask_api_key(str(resolved["bot_token"])),
        telegram_chat_id=str(resolved["chat_id"]),
        telegram_notify_on_error=bool(resolved["notify_on_error"]),
        telegram_notify_on_scan_summary=bool(resolved["notify_on_scan_summary"]),
        telegram_agent_enabled=bool(agent_cfg["enabled"]),
        telegram_agent_bot_token_masked=_mask_api_key(str(agent_cfg["bot_token"])),
        telegram_agent_allowed_chat_ids=",".join(agent_cfg["allowed_chat_ids"]),
        telegram_agent_token_conflict=bool(agent_cfg["token_conflict"]),
        source=str(resolved["source"]),
    )


@router.get("", response_model=NotificationSettingsResponse)
async def get_notification_settings(
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> NotificationSettingsResponse:
    return await _build_response(db)


@router.put("", response_model=NotificationSettingsResponse)
async def update_notification_settings(
    payload: NotificationSettingsUpdate,
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> NotificationSettingsResponse:
    settings = get_settings()
    current_notify = await resolve_telegram_settings(db)
    current_agent = await resolve_agent_bot_settings(db)

    candidate_notify_token = (
        payload.telegram_bot_token.strip()
        if payload.telegram_bot_token is not None
        else current_notify["bot_token"]
    )
    candidate_agent_token = (
        payload.telegram_agent_bot_token.strip()
        if payload.telegram_agent_bot_token is not None
        else current_agent["bot_token"]
    )
    candidate_agent_enabled = (
        payload.telegram_agent_enabled
        if payload.telegram_agent_enabled is not None
        else current_agent["enabled"]
    )
    if candidate_agent_enabled and has_token_conflict(candidate_notify_token, candidate_agent_token):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="独立双向女仆 Bot Token 不能与推送/Hermes 共用的 Bot Token 相同，请从 @BotFather 创建独立 Bot。",
        )

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
    if payload.telegram_agent_enabled is not None:
        await _upsert(
            db,
            "telegram_agent_enabled",
            "true" if payload.telegram_agent_enabled else "false",
        )
    if payload.telegram_agent_bot_token is not None:
        agent_tok = payload.telegram_agent_bot_token.strip()
        stored_agent = encrypt_password(agent_tok, settings.JWT_SECRET) if agent_tok else ""
        await _upsert(db, "telegram_agent_bot_token", stored_agent)
    if payload.telegram_agent_allowed_chat_ids is not None:
        await _upsert(
            db,
            "telegram_agent_allowed_chat_ids",
            payload.telegram_agent_allowed_chat_ids.strip(),
        )
    await db.commit()
    return await _build_response(db)


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
