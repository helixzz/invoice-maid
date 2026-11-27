from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.api.maid as maid_api
from app.models import InvoiceClaimTask, MaidMessage
from app.services.claim_form_agent import BrowserClaimOutcome
import app.services.maid_agent as maid_mod
from app.services.qr_receipt_decoder import DecodedReceipt


@pytest.mark.asyncio
async def test_profiles_crud_and_default_switch(client, auth_headers) -> None:
    assert (await client.get("/api/v1/profiles", headers=auth_headers)).json() == []

    p1 = (
        await client.post(
            "/api/v1/profiles",
            headers=auth_headers,
            json={"name": "公司A", "title": "上海A科技", "tax_id": "913100001", "email": "a@co.com", "is_default": True},
        )
    )
    assert p1.status_code == 201
    id1 = p1.json()["id"]

    p2 = (
        await client.post(
            "/api/v1/profiles",
            headers=auth_headers,
            json={"name": "公司B", "title": "北京B科技", "is_default": True},
        )
    )
    assert p2.status_code == 201
    id2 = p2.json()["id"]

    listed = (await client.get("/api/v1/profiles", headers=auth_headers)).json()
    assert listed[0]["id"] == id2 and listed[0]["is_default"] is True
    assert listed[1]["id"] == id1 and listed[1]["is_default"] is False

    upd = await client.put(
        f"/api/v1/profiles/{id1}",
        headers=auth_headers,
        json={"name": "公司A新版", "is_default": True},
    )
    assert upd.status_code == 200
    assert upd.json()["name"] == "公司A新版"
    assert upd.json()["is_default"] is True

    # PUT without is_default=True (covers non-default branch)
    upd_no_def = await client.put(
        f"/api/v1/profiles/{id1}",
        headers=auth_headers,
        json={"phone": "13900001111"},
    )
    assert upd_no_def.status_code == 200 and upd_no_def.json()["phone"] == "13900001111"

    assert (await client.delete(f"/api/v1/profiles/{id2}", headers=auth_headers)).status_code == 204


@pytest.mark.asyncio
async def test_maid_chat_memories_claims_and_screenshot_api(
    client, auth_headers, db, settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1) Chat + history + clear
    class StubRunner:
        def __init__(self, _settings):
            pass

        async def chat(self, _db, *, user_id, user_message, session_id):
            _db.add(MaidMessage(user_id=user_id, session_id=session_id, role="user", content=user_message))
            _db.add(MaidMessage(user_id=user_id, session_id=session_id, role="assistant", content="你好主人", tool_calls_json="{bad"))
            _db.add(MaidMessage(user_id=user_id, session_id=session_id, role="assistant", content="二号", tool_calls_json='{"x":1}'))
            await _db.commit()
            return SimpleNamespace(reply="你好主人", tool_events=[], session_id=session_id)

    monkeypatch.setattr(maid_api, "MaidAgentRunner", StubRunner)
    chat_res = await client.post("/api/v1/maid/chat", headers=auth_headers, json={"message": "hi", "session_id": "w1"})
    assert chat_res.status_code == 200
    assert chat_res.json()["reply"] == "你好主人"

    hist = await client.get("/api/v1/maid/history?session_id=w1", headers=auth_headers)
    assert hist.status_code == 200 and len(hist.json()) == 3
    assert (await client.delete("/api/v1/maid/history?session_id=w1", headers=auth_headers)).status_code == 204

    # 2) Memories CRUD
    mem = await client.post(
        "/api/v1/maid/memories",
        headers=auth_headers,
        json={"category": "reimbursement", "rule_text": "药房不报销", "match_pattern": "药房"},
    )
    assert mem.status_code == 201
    mem_id = mem.json()["id"]
    assert len((await client.get("/api/v1/maid/memories", headers=auth_headers)).json()) == 1
    assert (await client.delete(f"/api/v1/maid/memories/{mem_id}", headers=auth_headers)).status_code == 204

    # 3) Claims from photo (empty rejected, valid photo with 2 profiles -> needs_profile_choice)
    bad_photo = await client.post("/api/v1/maid/claims/from-photo", headers=auth_headers, files={"file": ("e.png", b"", "image/png")})
    assert bad_photo.status_code == 400

    p1 = (await client.post("/api/v1/profiles", headers=auth_headers, json={"name": "P1", "title": "Co1"})).json()
    await client.post("/api/v1/profiles", headers=auth_headers, json={"name": "P2", "title": "Co2"})

    monkeypatch.setattr(
        maid_mod,
        "analyze_receipt_photo",
        lambda _d, _a, _b: _coro(DecodedReceipt(qr_urls=["https://nnfp.jss.com.cn/p"], merchant_name="咖啡", amount=Decimal("25.00"))),
    )
    from_photo = await client.post(
        "/api/v1/maid/claims/from-photo",
        headers=auth_headers,
        files={"file": ("qr.png", b"fake-png", "image/png")},
    )
    assert from_photo.status_code == 201
    assert from_photo.json()["needs_profile_choice"] is True
    task_id = from_photo.json()["task"]["id"]

    # Claim from URL
    from_url = await client.post(
        "/api/v1/maid/claims/from-url",
        headers=auth_headers,
        json={"qr_url": "https://nnfp.jss.com.cn/url"},
    )
    assert from_url.status_code == 201

    # Retry before selecting profile -> 400
    assert (await client.post(f"/api/v1/maid/claims/{task_id}/retry", headers=auth_headers, json={})).status_code == 400

    # Screenshot missing -> 404
    assert (await client.get(f"/api/v1/maid/claims/{task_id}/screenshot", headers=auth_headers)).status_code == 404

    # Select profile (mock execute_claim_task)
    async def fake_exec(db, *, task, profile, settings, **kwargs):
        del kwargs
        task.profile_id = profile.id
        task.status = "needs_attention"
        task.attention_reason = "需要验证码"
        shot_dir = Path(settings.STORAGE_PATH) / "users" / str(task.user_id) / "claims"
        shot_dir.mkdir(parents=True, exist_ok=True)
        rel = f"users/{task.user_id}/claims/claim_{task.id}.png"
        (Path(settings.STORAGE_PATH) / rel).write_bytes(b"\x89PNG\r\n")
        task.last_screenshot_path = rel
        task.log_json = "{bad"
        await db.commit()
        return task

    monkeypatch.setattr(maid_api, "execute_claim_task", fake_exec)
    sel = await client.post(
        f"/api/v1/maid/claims/{task_id}/select-profile",
        headers=auth_headers,
        json={"profile_id": p1["id"]},
    )
    assert sel.status_code == 200
    assert sel.json()["task"]["has_screenshot"] is True

    # Screenshot download succeeds, then missing file on disk -> 404
    shot = await client.get(f"/api/v1/maid/claims/{task_id}/screenshot", headers=auth_headers)
    assert shot.status_code == 200
    task_row = await db.get(InvoiceClaimTask, task_id)
    (Path(settings.STORAGE_PATH) / task_row.last_screenshot_path).unlink()
    assert (await client.get(f"/api/v1/maid/claims/{task_id}/screenshot", headers=auth_headers)).status_code == 404

    # Non-list log_json edge case
    task_row.log_json = '{"not":"list"}'
    await db.commit()
    listed = await client.get("/api/v1/maid/claims", headers=auth_headers)
    assert listed.status_code == 200 and len(listed.json()) >= 2

    # Retry with captcha & cancel & heartbeat
    ret = await client.post(f"/api/v1/maid/claims/{task_id}/retry", headers=auth_headers, json={"captcha_input": "ABCD"})
    assert ret.status_code == 200
    can = await client.post(f"/api/v1/maid/claims/{task_id}/cancel", headers=auth_headers)
    assert can.status_code == 200 and can.json()["status"] == "cancelled"

    hb = await client.post("/api/v1/maid/heartbeat", headers=auth_headers)
    assert hb.status_code == 200
    assert "matched_claims" in hb.json()


@pytest.mark.asyncio
async def test_notification_settings_blocks_conflicting_agent_token(client, auth_headers) -> None:
    await client.put(
        "/api/v1/settings/notifications",
        headers=auth_headers,
        json={"telegram_enabled": True, "telegram_bot_token": "999:HERMES_SHARED", "telegram_chat_id": "100"},
    )
    conflict = await client.put(
        "/api/v1/settings/notifications",
        headers=auth_headers,
        json={"telegram_agent_enabled": True, "telegram_agent_bot_token": "999:HERMES_SHARED"},
    )
    assert conflict.status_code == 400

    ok = await client.put(
        "/api/v1/settings/notifications",
        headers=auth_headers,
        json={
            "telegram_agent_enabled": True,
            "telegram_agent_bot_token": "888:INDEPENDENT_MAID_BOT",
            "telegram_agent_allowed_chat_ids": "100",
        },
    )
    assert ok.status_code == 200
    assert ok.json()["telegram_agent_enabled"] is True
    assert ok.json()["telegram_agent_token_conflict"] is False


async def _coro(val):
    return val
