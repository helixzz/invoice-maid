from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.services.telegram_notifier as tg_mod
from app.models import AppSettings
from app.services.email_scanner import decrypt_password
from app.services.telegram_notifier import (
    _parse_bool,
    notify_account_error,
    notify_scan_summary,
    reset_alert_cooldowns,
    resolve_telegram_settings,
    send_telegram_message,
)


@pytest.mark.asyncio
async def test_notification_settings_crud_and_encryption(client, auth_headers, db, settings) -> None:
    res = await client.get("/api/v1/settings/notifications", headers=auth_headers)
    assert res.status_code == 200
    assert res.json() == {
        "telegram_enabled": False,
        "telegram_bot_token_masked": "",
        "telegram_chat_id": "",
        "telegram_notify_on_error": True,
        "telegram_notify_on_scan_summary": True,
        "telegram_agent_enabled": False,
        "telegram_agent_bot_token_masked": "",
        "telegram_agent_allowed_chat_ids": "",
        "telegram_agent_token_conflict": False,
        "source": "environment",
    }

    upd = await client.put(
        "/api/v1/settings/notifications",
        headers=auth_headers,
        json={
            "telegram_enabled": True,
            "telegram_bot_token": "123456:ABCDEF-token-9999",
            "telegram_chat_id": "987654321",
            "telegram_notify_on_error": True,
            "telegram_notify_on_scan_summary": False,
        },
    )
    assert upd.status_code == 200
    body = upd.json()
    assert body["telegram_enabled"] is True
    assert body["telegram_bot_token_masked"] == "12...9999"
    assert body["telegram_chat_id"] == "987654321"
    assert body["telegram_notify_on_scan_summary"] is False
    assert body["source"] == "database"

    stored = await db.get(AppSettings, "telegram_bot_token")
    assert stored is not None
    assert stored.value != "123456:ABCDEF-token-9999"
    assert decrypt_password(stored.value, settings.JWT_SECRET) == "123456:ABCDEF-token-9999"

    # Update existing rows & clear token; also exercise partial PUT omitting optional fields
    upd2 = await client.put(
        "/api/v1/settings/notifications",
        headers=auth_headers,
        json={"telegram_bot_token": "", "telegram_enabled": False},
    )
    assert upd2.status_code == 200
    assert upd2.json()["telegram_bot_token_masked"] == ""
    assert upd2.json()["telegram_enabled"] is False

    upd3 = await client.put(
        "/api/v1/settings/notifications",
        headers=auth_headers,
        json={"telegram_chat_id": "555"},
    )
    assert upd3.status_code == 200
    assert upd3.json()["telegram_chat_id"] == "555"

    # Clearing telegram_bot_token while telegram_agent_bot_token is set routes
    # one-way notifications cleanly through Miri's own bot with zero conflict
    upd4 = await client.put(
        "/api/v1/settings/notifications",
        headers=auth_headers,
        json={
            "telegram_bot_token": "",
            "telegram_agent_enabled": True,
            "telegram_agent_bot_token": "999888:MIRI-ONLY-TOKEN",
        },
    )
    assert upd4.status_code == 200
    assert upd4.json()["telegram_agent_token_conflict"] is False
    resolved_unified = await resolve_telegram_settings(db)
    assert resolved_unified["bot_token"] == "999888:MIRI-ONLY-TOKEN"

    # Empty agent token stays empty cleanly
    await client.put(
        "/api/v1/settings/notifications",
        headers=auth_headers,
        json={"telegram_agent_bot_token": ""},
    )
    resolved_empty = await resolve_telegram_settings(db)
    assert resolved_empty["bot_token"] == ""


@pytest.mark.asyncio
async def test_notification_test_endpoint_and_http_paths(
    client, auth_headers, db, monkeypatch: pytest.MonkeyPatch
) -> None:
    await client.put(
        "/api/v1/settings/notifications",
        headers=auth_headers,
        json={
            "telegram_enabled": True,
            "telegram_bot_token": "123456:TOKEN",
            "telegram_chat_id": "111222",
        },
    )

    sent_payloads: list[dict] = []

    class OkClient:
        def __init__(self, timeout: float = 10.0):
            del timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url: str, json: dict):
            sent_payloads.append({"url": url, "json": json})
            return SimpleNamespace(is_success=True, status_code=200, text="ok")

    monkeypatch.setattr(tg_mod.httpx, "AsyncClient", OkClient)
    res = await client.post("/api/v1/settings/notifications/test", headers=auth_headers)
    assert res.status_code == 200
    assert res.json() == {"ok": True, "detail": "ok"}
    assert "sendMessage" in sent_payloads[0]["url"]

    # Non-2xx response
    class BadStatusClient(OkClient):
        async def post(self, url: str, json: dict):
            del url, json
            return SimpleNamespace(is_success=False, status_code=401, text="Unauthorized")

    monkeypatch.setattr(tg_mod.httpx, "AsyncClient", BadStatusClient)
    ok, detail = await send_telegram_message("tok", "cid", "hi")
    assert ok is False
    assert "401" in detail

    # Network exception
    class CrashClient(OkClient):
        async def post(self, url: str, json: dict):
            del url, json
            raise RuntimeError("network down")

    monkeypatch.setattr(tg_mod.httpx, "AsyncClient", CrashClient)
    ok2, detail2 = await send_telegram_message("tok", "cid", "hi")
    assert ok2 is False
    assert "network down" in detail2

    # Missing config
    ok3, _ = await send_telegram_message("", "", "hi")
    assert ok3 is False


@pytest.mark.asyncio
async def test_notify_scan_summary_and_account_error_with_cooldown(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _parse_bool(None, True) is True
    assert _parse_bool(True, False) is True
    assert _parse_bool("false", True) is False

    # Disabled by default -> False
    assert await notify_scan_summary(db, account_name="A", emails_scanned=5, invoices_added=2) is False
    assert await notify_account_error(db, account_name="A", error_message="boom") is False

    # Partial DB rows (only telegram_enabled present -> omits telegram_chat_id)
    db.add(AppSettings(key="telegram_enabled", value="true"))
    await db.commit()
    partial = await resolve_telegram_settings(db)
    assert partial["enabled"] is True
    assert partial["chat_id"] == ""

    # Delete telegram_enabled and insert only telegram_chat_id -> omits telegram_enabled
    await db.delete(await db.get(AppSettings, "telegram_enabled"))
    db.add(AppSettings(key="telegram_chat_id", value="888"))
    await db.commit()
    partial0 = await resolve_telegram_settings(db)
    assert partial0["enabled"] is False
    assert partial0["chat_id"] == "888"

    db.add_all([
        AppSettings(key="telegram_enabled", value="true"),
        AppSettings(key="telegram_bot_token", value=""),
        AppSettings(key="telegram_notify_on_error", value="true"),
        AppSettings(key="telegram_notify_on_scan_summary", value="true"),
    ])
    await db.commit()
    # Partial rows check + empty token returns False
    resolved = await resolve_telegram_settings(db)
    assert resolved["bot_token"] == ""
    assert await notify_scan_summary(db, account_name="A", emails_scanned=5, invoices_added=2) is False

    # Enable with valid token
    monkeypatch.setattr(
        tg_mod,
        "resolve_telegram_settings",
        lambda _db: _coro({
            "enabled": True,
            "bot_token": "tok",
            "chat_id": "999",
            "notify_on_error": True,
            "notify_on_scan_summary": True,
            "source": "database",
        }),
    )
    messages: list[str] = []

    async def fake_send(token: str, chat_id: str, text: str, **kwargs):
        del token, chat_id, kwargs
        messages.append(text)
        return True, "ok"

    monkeypatch.setattr(tg_mod, "send_telegram_message", fake_send)
    reset_alert_cooldowns()

    # Circuit open + new invoices -> sends both alert & summary
    assert await notify_scan_summary(
        db, account_name="Outlook", emails_scanned=10, invoices_added=2, errors_count=3, circuit_open=True
    ) is True
    assert len(messages) == 2
    assert "熔断" in messages[0]
    assert "新增发票" in messages[1]

    # Second identical alert inside cooldown is suppressed; summary still sends if invoices_added > 0
    assert await notify_scan_summary(
        db, account_name="Outlook", emails_scanned=4, invoices_added=0, errors_count=1, circuit_open=True
    ) is False
    assert len(messages) == 2

    # Non-circuit error alert
    assert await notify_scan_summary(
        db, account_name="QQ", emails_scanned=2, invoices_added=0, errors_count=1, circuit_open=False
    ) is True
    assert "扫描异常" in messages[-1]

    # Zero errors + zero added -> no message sent
    assert await notify_scan_summary(
        db, account_name="QQ", emails_scanned=2, invoices_added=0, errors_count=0, circuit_open=False
    ) is False

    # Account error + cooldown
    assert await notify_account_error(db, account_name="QQ", error_message="IMAP auth failed") is True
    assert await notify_account_error(db, account_name="QQ", error_message="IMAP auth failed") is False


async def _coro(val):
    return val
