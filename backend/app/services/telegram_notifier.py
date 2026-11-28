from __future__ import annotations

import html
import logging
import time
from typing import Any, TypedDict

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AppSettings
from app.services.email_scanner import decrypt_password

logger = logging.getLogger(__name__)

TELEGRAM_SETTINGS_KEYS = (
    "telegram_enabled",
    "telegram_bot_token",
    "telegram_chat_id",
    "telegram_notify_on_error",
    "telegram_notify_on_scan_summary",
    "telegram_agent_enabled",
    "telegram_agent_bot_token",
)

ALERT_COOLDOWN_SECONDS = 900.0  # 15 minutes per alert key to prevent spam
_last_alert_sent: dict[str, float] = {}


class ResolvedTelegramSettings(TypedDict):
    enabled: bool
    bot_token: str
    chat_id: str
    notify_on_error: bool
    notify_on_scan_summary: bool
    source: str


def reset_alert_cooldowns() -> None:
    _last_alert_sent.clear()


def _parse_bool(raw: str | bool | None, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


async def resolve_telegram_settings(db: AsyncSession) -> ResolvedTelegramSettings:
    settings = get_settings()
    resolved: ResolvedTelegramSettings = {
        "enabled": bool(settings.TELEGRAM_ENABLED),
        "bot_token": settings.TELEGRAM_BOT_TOKEN,
        "chat_id": settings.TELEGRAM_CHAT_ID,
        "notify_on_error": bool(settings.TELEGRAM_NOTIFY_ON_ERROR),
        "notify_on_scan_summary": bool(settings.TELEGRAM_NOTIFY_ON_SCAN_SUMMARY),
        "source": "environment",
    }
    rows = {
        row.key: row.value
        for row in (
            await db.execute(select(AppSettings).where(AppSettings.key.in_(TELEGRAM_SETTINGS_KEYS)))
        ).scalars().all()
    }
    if rows:
        resolved["source"] = "database"
        if "telegram_enabled" in rows:
            resolved["enabled"] = _parse_bool(rows["telegram_enabled"], resolved["enabled"])
        if "telegram_bot_token" in rows:
            raw_token = rows["telegram_bot_token"]
            resolved["bot_token"] = (
                decrypt_password(raw_token, settings.JWT_SECRET) if raw_token else ""
            )
        if "telegram_chat_id" in rows:
            resolved["chat_id"] = rows["telegram_chat_id"]
        if "telegram_notify_on_error" in rows:
            resolved["notify_on_error"] = _parse_bool(
                rows["telegram_notify_on_error"], resolved["notify_on_error"]
            )
        if "telegram_notify_on_scan_summary" in rows:
            resolved["notify_on_scan_summary"] = _parse_bool(
                rows["telegram_notify_on_scan_summary"], resolved["notify_on_scan_summary"]
            )

    # When dedicated notification bot token is left blank, seamlessly route one-way
    # scan summaries and alerts through Miri's independent Agent Bot token so all
    # Invoice Maid messages stay in Miri's own Telegram channel (no Hermes overlap).
    if not resolved["bot_token"]:
        agent_token_plain = settings.TELEGRAM_AGENT_BOT_TOKEN
        if "telegram_agent_bot_token" in rows:
            raw_agent = rows["telegram_agent_bot_token"]
            agent_token_plain = (
                decrypt_password(raw_agent, settings.JWT_SECRET) if raw_agent else ""
            )
        if agent_token_plain:
            resolved["bot_token"] = agent_token_plain
    return resolved


async def send_telegram_message(
    bot_token: str,
    chat_id: str,
    text: str,
    *,
    timeout: float = 10.0,
) -> tuple[bool, str]:
    """Send an HTML-formatted message via the Telegram Bot API. Never raises."""
    if not bot_token or not chat_id:
        return False, "Telegram bot token or chat ID not configured"
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text[:4000],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
        if resp.is_success:
            return True, "ok"
        detail = f"HTTP {resp.status_code}: {resp.text[:300]}"
        logger.warning("Telegram sendMessage failed: %s", detail)
        return False, detail
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"[:300]
        logger.warning("Telegram sendMessage error: %s", detail)
        return False, detail


def _can_send_alert(alert_key: str) -> bool:
    now = time.monotonic()
    last = _last_alert_sent.get(alert_key)
    if last is not None and (now - last) < ALERT_COOLDOWN_SECONDS:
        return False
    _last_alert_sent[alert_key] = now
    return True


async def notify_scan_summary(
    db: AsyncSession,
    *,
    account_name: str,
    emails_scanned: int,
    invoices_added: int,
    errors_count: int = 0,
    circuit_open: bool = False,
) -> bool:
    """Send Telegram scan summary when new invoices arrive, and/or error alert on system failures."""
    cfg = await resolve_telegram_settings(db)
    if not cfg["enabled"] or not cfg["bot_token"] or not cfg["chat_id"]:
        return False

    sent_any = False
    safe_name = html.escape(account_name)

    if (circuit_open or errors_count > 0) and cfg["notify_on_error"]:
        alert_key = f"scan_error:{account_name}:{'circuit' if circuit_open else 'err'}"
        if _can_send_alert(alert_key):
            headline = (
                "🚨 <b>Invoice Maid 告警：LLM 熔断触发</b>"
                if circuit_open
                else "⚠️ <b>Invoice Maid 扫描异常</b>"
            )
            msg = (
                f"{headline}\n"
                f"账户：<code>{safe_name}</code>\n"
                f"扫描邮件：{emails_scanned} | 失败：{errors_count} | 新增发票：{invoices_added}\n"
                f"失败邮件已进入重试队列，待服务恢复后将自动补处理。"
            )
            ok, _ = await send_telegram_message(cfg["bot_token"], cfg["chat_id"], msg)
            sent_any = sent_any or ok

    if invoices_added > 0 and cfg["notify_on_scan_summary"]:
        msg = (
            f"🧾 <b>Invoice Maid 扫描汇总</b>\n"
            f"账户：<code>{safe_name}</code>\n"
            f"新增发票：<b>{invoices_added}</b> 张（本次共扫描 {emails_scanned} 封邮件）"
        )
        ok, _ = await send_telegram_message(cfg["bot_token"], cfg["chat_id"], msg)
        sent_any = sent_any or ok

    return sent_any


async def notify_account_error(
    db: AsyncSession,
    *,
    account_name: str,
    error_message: str,
) -> bool:
    """Send Telegram alert when an account-level scan crashes (e.g. OAuth/IMAP outage)."""
    cfg = await resolve_telegram_settings(db)
    if not cfg["enabled"] or not cfg["notify_on_error"] or not cfg["bot_token"] or not cfg["chat_id"]:
        return False
    alert_key = f"account_error:{account_name}"
    if not _can_send_alert(alert_key):
        return False

    safe_name = html.escape(account_name)
    safe_err = html.escape(error_message[:500])
    msg = (
        f"🚨 <b>Invoice Maid 账户扫描失败</b>\n"
        f"账户：<code>{safe_name}</code>\n"
        f"错误：<code>{safe_err}</code>"
    )
    ok, _ = await send_telegram_message(cfg["bot_token"], cfg["chat_id"], msg)
    return ok
