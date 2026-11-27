"""Conflict-free independent Telegram Agent Bot listener for Invoice Maid v2.0.

SAFETY GUARANTEE:
  To prevent ``409 Conflict: terminated by other getUpdates request`` when the
  one-way notification bot token is shared with Hermes Agent, this listener ONLY
  starts when ``telegram_agent_enabled=true`` AND a distinct
  ``telegram_agent_bot_token`` is configured (strictly different from
  ``telegram_bot_token``).
"""

from __future__ import annotations

import asyncio
import html
import logging
from typing import Any, TypedDict

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AppSettings, InvoiceClaimTask, InvoiceProfile, User
from app.services.ai_service import AIService
from app.services.claim_form_agent import BrowserRunner, execute_claim_task
from app.services.email_scanner import decrypt_password
from app.services.file_manager import FileManager
from app.services.maid_agent import (
    MaidAgentRunner,
    _summarize_claim_status,
    create_claim_from_receipt_photo,
)
from app.services.telegram_notifier import _parse_bool, resolve_telegram_settings

logger = logging.getLogger(__name__)

AGENT_BOT_SETTINGS_KEYS = (
    "telegram_agent_enabled",
    "telegram_agent_bot_token",
    "telegram_agent_allowed_chat_ids",
)


class ResolvedAgentBotSettings(TypedDict):
    enabled: bool
    bot_token: str
    allowed_chat_ids: list[str]
    token_conflict: bool


def parse_allowed_chat_ids(raw: str) -> list[str]:
    return [part.strip() for part in (raw or "").replace("\n", ",").split(",") if part.strip()]


def has_token_conflict(notify_token: str, agent_token: str) -> bool:
    """Return True if agent_token duplicates the shared notification/Hermes bot token."""
    return bool(agent_token and notify_token and agent_token.strip() == notify_token.strip())


async def resolve_agent_bot_settings(db: AsyncSession) -> ResolvedAgentBotSettings:
    settings = get_settings()
    enabled = bool(settings.TELEGRAM_AGENT_ENABLED)
    bot_token = settings.TELEGRAM_AGENT_BOT_TOKEN
    allowed_raw = settings.TELEGRAM_AGENT_ALLOWED_CHAT_IDS

    rows = {
        row.key: row.value
        for row in (
            await db.execute(select(AppSettings).where(AppSettings.key.in_(AGENT_BOT_SETTINGS_KEYS)))
        ).scalars().all()
    }
    if "telegram_agent_enabled" in rows:
        enabled = _parse_bool(rows["telegram_agent_enabled"], enabled)
    if "telegram_agent_bot_token" in rows:
        raw = rows["telegram_agent_bot_token"]
        bot_token = decrypt_password(raw, settings.JWT_SECRET) if raw else ""
    if "telegram_agent_allowed_chat_ids" in rows:
        allowed_raw = rows["telegram_agent_allowed_chat_ids"]

    notify_cfg = await resolve_telegram_settings(db)
    conflict = has_token_conflict(notify_cfg["bot_token"], bot_token)
    return {
        "enabled": enabled,
        "bot_token": bot_token,
        "allowed_chat_ids": parse_allowed_chat_ids(allowed_raw),
        "token_conflict": conflict,
    }


async def _tg_api_call(
    client: httpx.AsyncClient,
    bot_token: str,
    method: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    resp = await client.post(
        f"https://api.telegram.org/bot{bot_token}/{method}",
        json=payload,
    )
    resp.raise_for_status()
    return resp.json()


async def _download_tg_file(client: httpx.AsyncClient, bot_token: str, file_id: str) -> bytes:
    info = await _tg_api_call(client, bot_token, "getFile", {"file_id": file_id})
    file_path = info.get("result", {}).get("file_path") or ""
    if not file_path:
        return b""
    resp = await client.get(f"https://api.telegram.org/file/bot{bot_token}/{file_path}")
    resp.raise_for_status()
    return resp.content


async def _resolve_default_admin_user_id(db: AsyncSession) -> int | None:
    row = (
        await db.execute(
            select(User.id).where(User.is_admin.is_(True), User.is_active.is_(True)).order_by(User.id.asc()).limit(1)
        )
    ).scalar_one_or_none()
    return int(row) if row is not None else None


async def handle_telegram_update(
    db: AsyncSession,
    *,
    update: dict[str, Any],
    bot_token: str,
    allowed_chat_ids: list[str],
    client: httpx.AsyncClient,
    runner: MaidAgentRunner | None = None,
    browser_runner: BrowserRunner | None = None,
) -> str:
    """Process one Telegram Update (text chat, receipt photo, or inline profile selection)."""
    settings = get_settings()
    agent = runner or MaidAgentRunner(settings=settings, browser_runner=browser_runner)
    user_id = await _resolve_default_admin_user_id(db)
    if user_id is None:
        return "no_admin"

    # 1) Inline keyboard callback (profile selection or cancel)
    cb = update.get("callback_query")
    if isinstance(cb, dict):
        cb_id = str(cb.get("id") or "")
        data = str(cb.get("data") or "")
        chat_id = str((cb.get("message") or {}).get("chat", {}).get("id") or "")
        if allowed_chat_ids and chat_id not in allowed_chat_ids:
            return "unauthorized"

        await _tg_api_call(client, bot_token, "answerCallbackQuery", {"callback_query_id": cb_id})
        if data.startswith("claim_cancel:"):
            task_id = int(data.split(":", 1)[1])
            task = await db.get(InvoiceClaimTask, task_id)
            if task is not None and task.user_id == user_id:
                task.status = "cancelled"
                await db.commit()
            await _tg_api_call(client, bot_token, "sendMessage", {"chat_id": chat_id, "text": "已取消本次开票申请。"})
            return "claim_cancelled"

        if data.startswith("claim_profile:"):
            parts = data.split(":")
            task_id, profile_id = int(parts[1]), int(parts[2])
            task = await db.get(InvoiceClaimTask, task_id)
            profile = await db.get(InvoiceProfile, profile_id)
            if task is None or profile is None or task.user_id != user_id or profile.user_id != user_id:
                await _tg_api_call(client, bot_token, "sendMessage", {"chat_id": chat_id, "text": "开票任务或抬头不存在。"})
                return "not_found"

            await _tg_api_call(
                client,
                bot_token,
                "sendMessage",
                {"chat_id": chat_id, "text": f"收到！正在使用「{profile.name}」自动填表申请开票，请稍候…"},
            )
            task = await execute_claim_task(
                db,
                task=task,
                profile=profile,
                settings=settings,
                ai=AIService(settings),
                file_mgr=FileManager(settings.STORAGE_PATH),
                browser_runner=browser_runner,
            )
            await _tg_api_call(
                client,
                bot_token,
                "sendMessage",
                {"chat_id": chat_id, "text": _summarize_claim_status(task, profile)},
            )
            return f"claim_{task.status}"

        return "ignored_callback"

    # 2) Regular message (photo or text)
    msg = update.get("message")
    if not isinstance(msg, dict):
        return "ignored"
    chat_id = str((msg.get("chat") or {}).get("id") or "")
    if not chat_id:
        return "ignored"
    if allowed_chat_ids and chat_id not in allowed_chat_ids:
        return "unauthorized"

    photos = msg.get("photo")
    if isinstance(photos, list) and photos:
        file_id = str(photos[-1].get("file_id") or "")
        img_bytes = await _download_tg_file(client, bot_token, file_id)
        intake = await create_claim_from_receipt_photo(
            db,
            user_id=user_id,
            image_bytes=img_bytes,
            settings=settings,
            ai=AIService(settings),
            source_channel="telegram",
            source_chat_id=chat_id,
            browser_runner=browser_runner,
        )
        payload: dict[str, Any] = {"chat_id": chat_id, "text": intake.message}
        if intake.needs_profile_choice:
            keyboard = [
                [{"text": f"🏢 {p.name} ({p.title[:16]})", "callback_data": f"claim_profile:{intake.task.id}:{p.id}"}]
                for p in intake.profiles
            ]
            keyboard.append([{"text": "❌ 取消", "callback_data": f"claim_cancel:{intake.task.id}"}])
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        await _tg_api_call(client, bot_token, "sendMessage", payload)
        return "photo_handled"

    text = str(msg.get("text") or "").strip()
    if not text:
        return "ignored"

    result = await agent.chat(db, user_id=user_id, user_message=text, session_id=f"tg:{chat_id}")
    await _tg_api_call(
        client,
        bot_token,
        "sendMessage",
        {"chat_id": chat_id, "text": html.escape(result.reply)[:4000], "parse_mode": "HTML"},
    )
    return "text_handled"


_polling_task: asyncio.Task[Any] | None = None


async def _poll_loop() -> None:  # pragma: no cover - long-running background loop
    from app.database import get_db

    offset = 0
    while True:
        try:
            async for db in get_db():
                cfg = await resolve_agent_bot_settings(db)
                if not cfg["enabled"] or not cfg["bot_token"] or cfg["token_conflict"]:
                    if cfg["token_conflict"]:
                        logger.warning(
                            "Telegram Agent Bot polling skipped: bot token duplicates notification/Hermes token"
                        )
                    await asyncio.sleep(15.0)
                    break

                async with httpx.AsyncClient(timeout=35.0) as client:
                    resp = await client.get(
                        f"https://api.telegram.org/bot{cfg['bot_token']}/getUpdates",
                        params={"offset": offset, "timeout": 25},
                    )
                    if resp.status_code == 409:
                        logger.warning("Telegram Agent Bot 409 Conflict; backing off 60s")
                        await asyncio.sleep(60.0)
                        break
                    resp.raise_for_status()
                    updates = resp.json().get("result") or []
                    for upd in updates:
                        offset = max(offset, int(upd.get("update_id", 0)) + 1)
                        try:
                            await handle_telegram_update(
                                db,
                                update=upd,
                                bot_token=cfg["bot_token"],
                                allowed_chat_ids=cfg["allowed_chat_ids"],
                                client=client,
                            )
                        except Exception as exc:
                            logger.warning("Failed handling Telegram update: %s", exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Telegram Agent Bot poll loop error: %s", exc)
            await asyncio.sleep(10.0)


def start_telegram_agent_listener() -> None:  # pragma: no cover - lifecycle hook
    global _polling_task
    if _polling_task is not None and not _polling_task.done():
        return
    _polling_task = asyncio.create_task(_poll_loop())


def stop_telegram_agent_listener() -> None:  # pragma: no cover - lifecycle hook
    global _polling_task
    if _polling_task is not None:
        _polling_task.cancel()
        _polling_task = None
