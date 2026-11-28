from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import json
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.models import AppSettings, InvoiceClaimTask, InvoiceProfile, MaidMemory, User
from app.services.ai_service import AIService, LLMCircuitBreaker, LLMUnavailableError
from app.services.claim_form_agent import BrowserClaimOutcome
import app.services.maid_agent as maid_mod
from app.services.maid_agent import (
    MaidAgentRunner,
    apply_maid_memories_to_invoice,
    create_claim_from_receipt_photo,
    run_maid_heartbeat,
)
from app.services.qr_receipt_decoder import DecodedReceipt
import app.services.telegram_agent_bot as tg_bot_mod
from app.services.telegram_agent_bot import (
    handle_telegram_update,
    has_token_conflict,
    resolve_agent_bot_settings,
)


@pytest.mark.asyncio
async def test_ai_service_chat_with_tools_fallback_and_circuit(db, settings, monkeypatch: pytest.MonkeyPatch) -> None:
    settings.LLM_MODEL = "m1"
    settings.LLM_FALLBACK_MODELS = "m2"
    req = httpx.Request("POST", "http://127.0.0.1:3425/v1/chat/completions")
    attempts: list[str] = []

    class FlakyRaw:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        async def create(self, **kwargs):
            attempts.append(kwargs["model"])
            if kwargs["model"] == "m1":
                raise openai.APIConnectionError(request=req)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="done", tool_calls=[]))])

    monkeypatch.setattr("app.services.ai_service.AsyncOpenAI", lambda **kwargs: FlakyRaw())
    monkeypatch.setattr("app.services.ai_service.instructor.from_openai", lambda c, mode: c)

    ai = AIService(settings, circuit_breaker=LLMCircuitBreaker(failure_threshold=2))
    msg = await ai.chat_with_tools(db, [{"role": "user", "content": "hi"}], tools=[{"type": "function"}])
    assert msg.content == "done"
    assert attempts == ["m1", "m2"]

    # Always fail upstream -> trips breaker
    class DeadRaw:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        async def create(self, **kwargs):
            del kwargs
            raise openai.APIConnectionError(request=req)

    monkeypatch.setattr("app.services.ai_service.AsyncOpenAI", lambda **kwargs: DeadRaw())
    breaker = LLMCircuitBreaker(failure_threshold=1)
    ai2 = AIService(settings, circuit_breaker=breaker)
    with pytest.raises(LLMUnavailableError):
        await ai2.chat_with_tools(db, [{"role": "user", "content": "hi"}])
    assert breaker.is_open is True

    # Non-upstream error does not trip breaker
    class ValueFailRaw:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        async def create(self, **kwargs):
            del kwargs
            raise ValueError("bad arg")

    monkeypatch.setattr("app.services.ai_service.AsyncOpenAI", lambda **kwargs: ValueFailRaw())
    breaker2 = LLMCircuitBreaker(failure_threshold=1)
    ai3 = AIService(settings, circuit_breaker=breaker2)
    with pytest.raises(LLMUnavailableError):
        await ai3.chat_with_tools(db, [{"role": "user", "content": "hi"}])
    assert breaker2.is_open is False


@pytest.mark.asyncio
async def test_maid_agent_tool_calling_loop_and_memories_and_heartbeat(
    db, settings, create_email_account, create_invoice, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = await create_email_account()
    inv = await create_invoice(
        email_account=account,
        invoice_no="MAID-001",
        seller="京东大药房",
        amount=Decimal("65.50"),
        currency="CNY",
        invoice_date=date(2026, 11, 26),
    )

    # Test apply_maid_memories_to_invoice (empty -> False, then with invalid/non-dict/valid rules)
    assert await apply_maid_memories_to_invoice(db, inv) is False
    db.add_all([
        MaidMemory(user_id=account.user_id, category="reimbursement", rule_text="no pattern", match_pattern="", action_json="{}"),
        MaidMemory(user_id=account.user_id, category="reimbursement", rule_text="bad json", match_pattern="大药房", action_json="{bad"),
        MaidMemory(user_id=account.user_id, category="reimbursement", rule_text="non dict", match_pattern="大药房", action_json="[]"),
        MaidMemory(user_id=account.user_id, category="reimbursement", rule_text="empty action", match_pattern="大药房", action_json=json.dumps({"reimbursement_status": "invalid", "tags": ""})),
        MaidMemory(
            user_id=account.user_id,
            category="reimbursement",
            rule_text="大药房无需报销",
            match_pattern="大药房",
            action_json=json.dumps({"reimbursement_status": "not_applicable", "tags": "个人,药房"}),
        ),
    ])
    await db.commit()
    assert await apply_maid_memories_to_invoice(db, inv) is True
    assert inv.reimbursement_status == "not_applicable"
    assert "个人" in inv.tags and "药房" in inv.tags
    # Duplicate tag idempotency
    assert await apply_maid_memories_to_invoice(db, inv) is True

    profile = InvoiceProfile(user_id=account.user_id, name="公司", title="Acme", tax_id="123", email="a@b.com", is_default=True)
    db.add(profile)
    await db.commit()
    await db.refresh(profile)

    claim = InvoiceClaimTask(user_id=account.user_id, qr_url="https://nnfp.jss.com.cn/c1", status="awaiting_profile")
    db.add(claim)
    await db.commit()
    await db.refresh(claim)

    async def fake_browser(_u, _p, _c):
        return BrowserClaimOutcome(kind="submitted", detail="已提交")

    # Sequence of tool calls covering ALL 9 tools + unknown tool + bad JSON args
    tool_calls_batch = [
        SimpleNamespace(id="c1", function=SimpleNamespace(name="search_invoices", arguments=json.dumps({"query": "", "currency": "CNY", "date_from": "2026-11-01", "date_to": "2026-11-30"}))),
        SimpleNamespace(id="c2", function=SimpleNamespace(name="get_spend_summary", arguments="{}")),
        SimpleNamespace(id="c3", function=SimpleNamespace(name="update_reimbursement", arguments=json.dumps({"invoice_ids": [inv.id], "reimbursement_status": "reimbursed", "tags": "done"}))),
        SimpleNamespace(id="c3b", function=SimpleNamespace(name="update_reimbursement", arguments=json.dumps({"invoice_ids": []}))),
        SimpleNamespace(id="c4", function=SimpleNamespace(name="list_invoice_profiles", arguments="{}")),
        SimpleNamespace(id="c5", function=SimpleNamespace(name="list_claim_tasks", arguments=json.dumps({"status": "awaiting_profile"}))),
        SimpleNamespace(id="c6", function=SimpleNamespace(name="select_claim_profile", arguments=json.dumps({"task_id": claim.id, "profile_id": profile.id}))),
        SimpleNamespace(id="c6b", function=SimpleNamespace(name="select_claim_profile", arguments=json.dumps({"task_id": 9999, "profile_id": profile.id}))),
        SimpleNamespace(id="c6c", function=SimpleNamespace(name="select_claim_profile", arguments=json.dumps({"task_id": claim.id, "profile_id": 9999}))),
        SimpleNamespace(id="c7", function=SimpleNamespace(name="retry_claim_task", arguments=json.dumps({"task_id": claim.id, "captcha_input": "1234"}))),
        SimpleNamespace(id="c7b", function=SimpleNamespace(name="retry_claim_task", arguments=json.dumps({"task_id": 9999}))),
        SimpleNamespace(id="c8", function=SimpleNamespace(name="list_memories", arguments="{}")),
        SimpleNamespace(id="c9", function=SimpleNamespace(name="save_memory_rule", arguments=json.dumps({"rule_text": "打车标出差", "match_pattern": "滴滴", "reimbursement_status": "unreimbursed", "tags": "出差"}))),
        SimpleNamespace(id="c10", function=SimpleNamespace(name="unknown_tool", arguments="not-json")),
        SimpleNamespace(id="c11", function=SimpleNamespace(name="list_memories", arguments="[]")),
    ]
    step = 0

    class FakeAgentAI:
        async def chat_with_tools(self, _db, _msgs, tools=None):
            nonlocal step
            del tools
            step += 1
            if step == 1:
                return SimpleNamespace(content="", tool_calls=tool_calls_batch)
            return SimpleNamespace(content="全部查询与操作已完成！", tool_calls=[])

    runner = MaidAgentRunner(settings=settings, ai=FakeAgentAI(), browser_runner=fake_browser)
    res = await runner.chat(db, user_id=account.user_id, user_message="帮我查账并处理开票")
    assert "完成" in res.reply
    assert len(res.tool_events) == len(tool_calls_batch)

    # Also test MAX_AGENT_STEPS exhaustion path
    class LoopForeverAI:
        async def chat_with_tools(self, _db, _msgs, tools=None):
            del tools
            return SimpleNamespace(
                content="",
                tool_calls=[SimpleNamespace(id="cx", function=SimpleNamespace(name="list_memories", arguments="{}"))],
            )

    runner_loop = MaidAgentRunner(settings=settings, ai=LoopForeverAI())
    res_loop = await runner_loop.chat(db, user_id=account.user_id, user_message="loop", session_id="s-loop")
    assert "已完成" in res_loop.reply

    # Also test retry_claim_task when profile was deleted after task referenced it
    t_orphan = InvoiceClaimTask(user_id=account.user_id, qr_url="https://x", profile_id=99999, status="needs_attention")
    t_done = InvoiceClaimTask(user_id=account.user_id, qr_url="https://done", status="completed")
    db.add_all([t_orphan, t_done])
    await db.commit()
    await db.refresh(t_orphan)
    await db.refresh(t_done)
    orphan_out = await runner._execute_tool(db, user_id=account.user_id, name="retry_claim_task", args={"task_id": t_orphan.id})
    assert "error" in orphan_out

    # Test new v2.4.0 tools: cancel_claim_tasks, update_invoice, save_invoice_profile, delete_memory_rule, trigger_email_scan
    assert (await runner._execute_tool(db, user_id=account.user_id, name="cancel_claim_tasks", args={}))["cancelled"] == 0
    c_ids = await runner._execute_tool(db, user_id=account.user_id, name="cancel_claim_tasks", args={"task_ids": [t_done.id, t_orphan.id]})
    assert c_ids["cancelled"] == 1 and t_orphan.id in c_ids["task_ids"]
    t_stuck = InvoiceClaimTask(user_id=account.user_id, qr_url="https://stuck", status="needs_attention")
    db.add(t_stuck)
    await db.commit()
    await db.refresh(t_stuck)
    c_stuck = await runner._execute_tool(db, user_id=account.user_id, name="cancel_claim_tasks", args={"cancel_stuck": True})
    assert c_stuck["cancelled"] >= 1

    assert "error" in await runner._execute_tool(db, user_id=account.user_id, name="update_invoice", args={"invoice_id": 99999})
    u_inv = await runner._execute_tool(
        db,
        user_id=account.user_id,
        name="update_invoice",
        args={"invoice_id": inv.id, "reimbursement_status": "pending", "reimbursement_notes": "客户招待", "tags": "餐饮", "invoice_category": "vat_invoice"},
    )
    assert u_inv["updated"] is True
    await db.refresh(inv)
    assert inv.notes == "客户招待"
    # Also exercise update_invoice omitting optional fields
    assert (await runner._execute_tool(db, user_id=account.user_id, name="update_invoice", args={"invoice_id": inv.id}))["updated"] is True

    assert "error" in await runner._execute_tool(db, user_id=account.user_id, name="save_invoice_profile", args={"profile_id": 99999})
    assert "error" in await runner._execute_tool(db, user_id=account.user_id, name="save_invoice_profile", args={"name": "only-name"})
    p_new = await runner._execute_tool(
        db,
        user_id=account.user_id,
        name="save_invoice_profile",
        args={"name": "新抬头", "title": "新科技有限公司", "tax_id": "9131NEW", "is_default": True},
    )
    assert p_new["is_default"] is True
    p_upd = await runner._execute_tool(
        db,
        user_id=account.user_id,
        name="save_invoice_profile",
        args={"profile_id": p_new["profile_id"], "email": "new@co.com", "is_default": False},
    )
    assert p_upd["profile_id"] == p_new["profile_id"]
    p_upd2 = await runner._execute_tool(
        db,
        user_id=account.user_id,
        name="save_invoice_profile",
        args={"profile_id": p_new["profile_id"]},
    )
    assert p_upd2["profile_id"] == p_new["profile_id"]
    await db.delete(await db.get(InvoiceProfile, p_new["profile_id"]))
    await db.commit()

    assert "error" in await runner._execute_tool(db, user_id=account.user_id, name="delete_memory_rule", args={"memory_id": 99999})
    m_saved = await runner._execute_tool(db, user_id=account.user_id, name="save_memory_rule", args={"rule_text": "临时规则"})
    m_del = await runner._execute_tool(db, user_id=account.user_id, name="delete_memory_rule", args={"memory_id": m_saved["saved_memory_id"]})
    assert m_del["deleted"] is True

    import app.tasks.scheduler as sched_mod

    async def fake_scan_all():
        return None

    monkeypatch.setattr(sched_mod, "scan_all_accounts", fake_scan_all)
    scan_res = await runner._execute_tool(db, user_id=account.user_id, name="trigger_email_scan", args={})
    assert scan_res["triggered"] is True

    # Test create_claim_from_receipt_photo: 0 profiles, 1 profile (auto), 2 profiles (prompt), no QR + code_type variants
    await db.delete(profile)
    await db.commit()
    monkeypatch.setattr(maid_mod, "analyze_receipt_photo", lambda _d, _a, _b: _coro(DecodedReceipt(qr_urls=[])))
    r_no_qr = await create_claim_from_receipt_photo(db, user_id=account.user_id, image_bytes=b"img", settings=settings, ai=None)
    assert r_no_qr.task.status == "needs_attention"

    # WeChat Mini Program code with merchant+amount -> tracked as submitted with clear explanation
    monkeypatch.setattr(
        maid_mod,
        "analyze_receipt_photo",
        lambda _d, _a, _b: _coro(DecodedReceipt(qr_urls=[], code_type="wechat_miniprogram_code", merchant_name="喜茶", amount=Decimal("28.00"))),
    )
    r_wx = await create_claim_from_receipt_photo(db, user_id=account.user_id, image_bytes=b"img", settings=settings, ai=None)
    assert r_wx.task.status == "submitted" and "微信小程序码" in r_wx.message

    # Alipay code + unreadable standard QR
    monkeypatch.setattr(
        maid_mod,
        "analyze_receipt_photo",
        lambda _d, _a, _b: _coro(DecodedReceipt(qr_urls=[], code_type="alipay_code", merchant_name="盒马")),
    )
    r_ali = await create_claim_from_receipt_photo(db, user_id=account.user_id, image_bytes=b"img", settings=settings, ai=None)
    assert r_ali.task.status == "needs_attention" and "支付宝" in r_ali.message

    monkeypatch.setattr(
        maid_mod,
        "analyze_receipt_photo",
        lambda _d, _a, _b: _coro(DecodedReceipt(qr_urls=[], code_type="standard_qr")),
    )
    r_blur = await create_claim_from_receipt_photo(db, user_id=account.user_id, image_bytes=b"img", settings=settings, ai=None)
    assert r_blur.task.status == "needs_attention" and "方形二维码" in r_blur.message

    monkeypatch.setattr(
        maid_mod,
        "analyze_receipt_photo",
        lambda _d, _a, _b: _coro(DecodedReceipt(qr_urls=["https://nnfp.jss.com.cn/a"], merchant_name="咖啡店", amount=Decimal("30.00"))),
    )
    r_zero = await create_claim_from_receipt_photo(db, user_id=account.user_id, image_bytes=b"img", settings=settings, ai=None)
    assert r_zero.needs_profile_choice is False and r_zero.task.status == "awaiting_profile"

    p1 = InvoiceProfile(user_id=account.user_id, name="抬头1", title="公司1", tax_id="9131")
    db.add(p1)
    await db.commit()
    # WeChat Mini Program code with existing profile includes copyable profile card
    monkeypatch.setattr(
        maid_mod,
        "analyze_receipt_photo",
        lambda _d, _a, _b: _coro(DecodedReceipt(qr_urls=[], code_type="wechat_miniprogram_code")),
    )
    r_wx_card = await create_claim_from_receipt_photo(db, user_id=account.user_id, image_bytes=b"img", settings=settings, ai=None)
    assert "公司1" in r_wx_card.message and "9131" in r_wx_card.message

    monkeypatch.setattr(
        maid_mod,
        "analyze_receipt_photo",
        lambda _d, _a, _b: _coro(DecodedReceipt(qr_urls=["https://nnfp.jss.com.cn/a"], merchant_name="咖啡店", amount=Decimal("30.00"))),
    )
    r_one = await create_claim_from_receipt_photo(db, user_id=account.user_id, image_bytes=b"img", settings=settings, ai=None, browser_runner=fake_browser)
    assert r_one.task.status == "submitted"

    p2 = InvoiceProfile(user_id=account.user_id, name="抬头2", title="公司2")
    db.add(p2)
    await db.commit()
    r_two = await create_claim_from_receipt_photo(db, user_id=account.user_id, image_bytes=b"img", settings=settings, ai=None)
    assert r_two.needs_profile_choice is True and len(r_two.profiles) == 2

    # Cover _summarize_claim_status for completed + wechat_only + needs_attention, and chat with tool role in history + no memories
    from app.models import MaidMessage
    from app.services.maid_agent import _summarize_claim_status

    assert "归档完成" in _summarize_claim_status(SimpleNamespace(status="completed", matched_invoice_id=1, qr_url="", attention_reason=""))
    assert "微信" in _summarize_claim_status(SimpleNamespace(status="needs_attention", matched_invoice_id=None, qr_url="weixin://x", attention_reason="仅限微信"))
    assert "确认" in _summarize_claim_status(SimpleNamespace(status="needs_attention", matched_invoice_id=None, qr_url="https://x", attention_reason="报错"))

    # Add tool-role message in history to cover non-user/assistant skip + single-field update_reimbursement + list_claim_tasks without status + save_memory_rule minimal
    db.add(MaidMessage(user_id=account.user_id, session_id="s-edge", role="tool", content="{}"))
    await db.commit()
    await runner._execute_tool(db, user_id=account.user_id, name="update_reimbursement", args={"invoice_ids": [inv.id]})
    await runner._execute_tool(db, user_id=account.user_id, name="list_claim_tasks", args={})
    await runner._execute_tool(db, user_id=account.user_id, name="save_memory_rule", args={"rule_text": "普通规则"})
    # Chat for a user with zero memories
    class SimpleReplyAI:
        async def chat_with_tools(self, _db, _msgs, tools=None):
            del tools
            return SimpleNamespace(content="ok", tool_calls=[])

    await MaidAgentRunner(settings=settings, ai=SimpleReplyAI()).chat(db, user_id=999999, user_message="hi", session_id="s-nomem")
    await MaidAgentRunner(settings=settings, ai=SimpleReplyAI()).chat(db, user_id=account.user_id, user_message="hi", session_id="s-edge")

    import app.services.telegram_notifier as tg_not_mod

    # Heartbeat when telegram disabled covers false branch on matched notification
    t_q2 = InvoiceClaimTask(user_id=account.user_id, profile_id=p1.id, qr_url="https://nnfp.jss.com.cn/q3", status="submitted", merchant_name="京东大药房", expected_amount=Decimal("65.50"))
    db.add(t_q2)
    await db.commit()
    monkeypatch.setattr(
        tg_not_mod,
        "resolve_telegram_settings",
        lambda _d: _coro({"enabled": False, "bot_token": "", "chat_id": "", "notify_on_error": False, "notify_on_scan_summary": False, "source": "database"}),
    )
    hb2 = await run_maid_heartbeat(db, settings=settings, ai=FakeAgentAI(), browser_runner=fake_browser)
    assert hb2["matched_claims"] >= 1

    # Heartbeat processes queued tasks and sends Telegram on matched claims
    t_q = InvoiceClaimTask(user_id=account.user_id, profile_id=p1.id, qr_url="https://nnfp.jss.com.cn/q", status="queued", merchant_name="京东大药房", expected_amount=Decimal("65.50"))
    t_q_missing = InvoiceClaimTask(user_id=account.user_id, profile_id=999999, qr_url="https://nnfp.jss.com.cn/q2", status="queued")
    db.add_all([t_q, t_q_missing])
    await db.commit()

    sent_tg: list[str] = []
    monkeypatch.setattr(
        tg_not_mod,
        "resolve_telegram_settings",
        lambda _d: _coro({"enabled": True, "bot_token": "t", "chat_id": "1", "notify_on_error": True, "notify_on_scan_summary": True, "source": "database"}),
    )
    monkeypatch.setattr(tg_not_mod, "send_telegram_message", lambda tok, cid, txt: _coro((sent_tg.append(txt) or True, "ok")))

    hb = await run_maid_heartbeat(db, settings=settings, ai=FakeAgentAI(), browser_runner=fake_browser)
    assert hb["processed_queued"] == 1
    assert hb["matched_claims"] >= 1
    assert len(sent_tg) >= 1


@pytest.mark.asyncio
async def test_telegram_agent_bot_conflict_guard_and_update_flows(
    db, settings, create_email_account, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert has_token_conflict("123:AAA", "123:AAA") is True
    assert has_token_conflict("123:AAA", "456:BBB") is False
    assert has_token_conflict("", "456:BBB") is False

    # Settings resolution from DB + token conflict detection
    from app.services.email_scanner import encrypt_password

    db.add_all([
        AppSettings(key="telegram_bot_token", value=encrypt_password("111:SAME", settings.JWT_SECRET)),
        AppSettings(key="telegram_agent_enabled", value="true"),
        AppSettings(key="telegram_agent_bot_token", value=encrypt_password("111:SAME", settings.JWT_SECRET)),
        AppSettings(key="telegram_agent_allowed_chat_ids", value="1001, 1002"),
    ])
    await db.commit()
    cfg = await resolve_agent_bot_settings(db)
    assert cfg["enabled"] is True
    assert cfg["token_conflict"] is True
    assert cfg["allowed_chat_ids"] == ["1001", "1002"]

    sent_calls: list[tuple[str, dict]] = []

    class FakeHttpClient:
        def __init__(self, *, return_msg_id: bool = True):
            self.return_msg_id = return_msg_id

        async def post(self, url: str, json: dict | None = None, data: dict | None = None, files: dict | None = None):
            method = url.rsplit("/", 1)[-1]
            sent_calls.append((method, json if json is not None else {"data": data, "has_files": bool(files)}))
            if method == "getFile":
                return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"result": {"file_path": "photos/p1.jpg"}})
            if method == "sendMessage" and self.return_msg_id:
                return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"ok": True, "result": {"message_id": 500}})
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"ok": True})

        async def get(self, url: str):
            del url
            return SimpleNamespace(raise_for_status=lambda: None, content=b"fake-jpg")

    client = FakeHttpClient()

    # Create profiles for admin user (seeded by conftest)
    admin = (await db.execute(__import__("sqlalchemy").select(User).where(User.is_admin.is_(True)))).scalar_one()
    p1 = InvoiceProfile(user_id=admin.id, name="公司A", title="A科技有限公司")
    p2 = InvoiceProfile(user_id=admin.id, name="公司B", title="B科技有限公司")
    db.add_all([p1, p2])
    await db.commit()
    await db.refresh(p1)
    await db.refresh(p2)

    # 1) Unauthorized chat rejected
    assert await handle_telegram_update(
        db, update={"message": {"chat": {"id": 9999}, "text": "hi"}}, bot_token="tok", allowed_chat_ids=["1001"], client=client
    ) == "unauthorized"
    assert await handle_telegram_update(
        db, update={"callback_query": {"id": "cb1", "data": "x", "message": {"chat": {"id": 9999}}}}, bot_token="tok", allowed_chat_ids=["1001"], client=client
    ) == "unauthorized"

    # 2) Ignored updates
    assert await handle_telegram_update(db, update={}, bot_token="tok", allowed_chat_ids=[], client=client) == "ignored"
    assert await handle_telegram_update(db, update={"message": {"chat": {}}}, bot_token="tok", allowed_chat_ids=[], client=client) == "ignored"
    assert await handle_telegram_update(db, update={"message": {"chat": {"id": 1001}, "text": "  "}}, bot_token="tok", allowed_chat_ids=["1001"], client=client) == "ignored"

    # 3) Text chat delegates to MaidAgentRunner
    class StubRunner:
        async def chat(self, _db, *, user_id, user_message, session_id):
            del user_id, user_message, session_id
            return SimpleNamespace(reply="收到，共 3 张未报销发票")

    assert await handle_telegram_update(
        db,
        update={"message": {"chat": {"id": 1001}, "text": "查账"}},
        bot_token="tok",
        allowed_chat_ids=["1001"],
        client=client,
        runner=StubRunner(),
    ) == "text_handled"

    # 4) Photo upload with 2 profiles -> sends instant ack then edits in-place with inline keyboard
    monkeypatch.setattr(
        maid_mod,
        "analyze_receipt_photo",
        lambda _d, _a, _b: _coro(DecodedReceipt(qr_urls=["https://nnfp.jss.com.cn/tg"], merchant_name="餐厅", amount=Decimal("120.00"))),
    )
    assert await handle_telegram_update(
        db,
        update={"message": {"chat": {"id": 1001}, "photo": [{"file_id": "f-small"}, {"file_id": "f-big"}]}},
        bot_token="tok",
        allowed_chat_ids=["1001"],
        client=client,
    ) == "photo_handled"
    last_method, last_payload = sent_calls[-1]
    assert last_method == "editMessageText"
    assert "inline_keyboard" in last_payload["reply_markup"]
    cb_data = last_payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    task_id = int(cb_data.split(":")[1])

    # Also test fallback when progress sendMessage returns no message_id (or edit fails)
    no_id_client = FakeHttpClient(return_msg_id=False)
    assert await handle_telegram_update(
        db,
        update={"message": {"chat": {"id": 1001}, "photo": [{"file_id": "f-noid"}]}},
        bot_token="tok",
        allowed_chat_ids=["1001"],
        client=no_id_client,
    ) == "photo_handled"
    assert sent_calls[-1][0] == "sendMessage"
    assert "inline_keyboard" in sent_calls[-1][1]["reply_markup"]

    # Cover typing / progress send & edit exception swallowing + on_progress callback execution
    class AllFailClient(FakeHttpClient):
        async def post(self, url: str, json: dict | None = None, data: dict | None = None, files: dict | None = None):
            raise RuntimeError("tg fail")

    await tg_bot_mod._tg_send_typing(AllFailClient(), "tok", "1001")
    assert await tg_bot_mod._tg_send_progress(AllFailClient(), "tok", "1001", "hi") is None
    assert await tg_bot_mod._tg_update_progress(AllFailClient(), "tok", "1001", 10, "hi") is False
    assert await tg_bot_mod._tg_update_progress(client, "tok", "1001", None, "hi") is False

    # 5) Callback selects profile -> collapses inline keyboard & executes claim with screenshot sent via sendPhoto
    async def shot_browser(_u, _p, _c):
        return BrowserClaimOutcome(kind="needs_attention", detail="获取用户信息异常", screenshot_bytes=b"\x89PNGfake", page_text_excerpt="获取用户信息异常")

    assert await handle_telegram_update(
        db,
        update={
            "callback_query": {
                "id": "cb2",
                "data": cb_data,
                "message": {"chat": {"id": 1001}, "message_id": 77, "text": "请选择抬头："},
            }
        },
        bot_token="tok",
        allowed_chat_ids=["1001"],
        client=client,
        browser_runner=shot_browser,
    ) == "claim_needs_attention"
    methods_called = [m for m, _ in sent_calls]
    assert "editMessageText" in methods_called
    assert "sendPhoto" in methods_called

    # Test dismiss keyboard fallback (no orig_text -> editMessageReplyMarkup; error swallowed) and long caption + sendPhoto failure
    await tg_bot_mod._dismiss_inline_keyboard(client, "tok", "1001", 88, "", "done")
    assert sent_calls[-1][0] == "editMessageReplyMarkup"

    class FailingPostClient(FakeHttpClient):
        async def post(self, url: str, json: dict | None = None, data: dict | None = None, files: dict | None = None):
            if url.endswith(("/editMessageText", "/sendPhoto")):
                raise RuntimeError("tg err")
            return await super().post(url, json=json, data=data, files=files)

    await tg_bot_mod._dismiss_inline_keyboard(FailingPostClient(), "tok", "1001", 88, "hi", "done")
    task_obj = await db.get(InvoiceClaimTask, task_id)
    # Long caption (>950 chars) sends photo + separate sendMessage
    await tg_bot_mod._send_claim_outcome_to_telegram(client, "tok", "1001", task_obj, p1, settings, override_text="A" * 1000)
    assert sent_calls[-2][0] == "sendPhoto" and sent_calls[-1][0] == "sendMessage"
    # sendPhoto fails -> falls back cleanly to sendMessage
    await tg_bot_mod._send_claim_outcome_to_telegram(FailingPostClient(), "tok", "1001", task_obj, p1, settings, override_text="short")
    # Missing screenshot file on disk -> falls back cleanly to sendMessage
    task_obj.last_screenshot_path = "users/1/claims/missing.png"
    await tg_bot_mod._send_claim_outcome_to_telegram(client, "tok", "1001", task_obj, p1, settings, override_text="no file")

    async def ok_browser(_u, _p, _c):
        return BrowserClaimOutcome(kind="submitted", detail="已提交")

    # Callback cancel (both existing & missing task), not_found, ignored_callback
    assert await handle_telegram_update(
        db,
        update={"callback_query": {"id": "cb3", "data": f"claim_cancel:{task_id}", "message": {"chat": {"id": 1001}}}},
        bot_token="tok",
        allowed_chat_ids=["1001"],
        client=client,
    ) == "claim_cancelled"
    assert await handle_telegram_update(
        db,
        update={"callback_query": {"id": "cb3b", "data": "claim_cancel:999999", "message": {"chat": {"id": 1001}}}},
        bot_token="tok",
        allowed_chat_ids=["1001"],
        client=client,
    ) == "claim_cancelled"

    # Photo upload with single profile -> auto-submits without inline keyboard (also covers no-id branch & on_progress)
    await db.delete(p2)
    await db.commit()
    assert await handle_telegram_update(
        db,
        update={"message": {"chat": {"id": 1001}, "photo": [{"file_id": "f-one"}]}},
        bot_token="tok",
        allowed_chat_ids=["1001"],
        client=client,
        browser_runner=ok_browser,
    ) == "photo_handled"
    assert "reply_markup" not in sent_calls[-1][1]

    # Test markdown_to_telegram_html formatting + HTML parse-error plain-text fallback
    assert tg_bot_mod.markdown_to_telegram_html("") == ""
    rendered = tg_bot_mod.markdown_to_telegram_html(
        "### 标题\n- **任务 #9**：税号 `9131<test>` ~~旧~~ [详情](https://a.b/c?x=1&y=2)\n```json\n{\"a\": 1}\n```"
    )
    assert "<b>标题</b>" in rendered
    assert "• <b>任务 #9</b>" in rendered
    assert "<code>9131&lt;test&gt;</code>" in rendered
    assert "<s>旧</s>" in rendered
    assert '<a href="https://a.b/c?x=1&amp;y=2">详情</a>' in rendered
    assert "<pre>" in rendered

    class HtmlFailOnceClient(FakeHttpClient):
        async def post(self, url: str, json: dict | None = None, data: dict | None = None, files: dict | None = None):
            if json and json.get("parse_mode") == "HTML":
                raise RuntimeError("can't parse entities")
            return await super().post(url, json=json, data=data, files=files)

    await tg_bot_mod._tg_send_formatted_message(HtmlFailOnceClient(), "tok", "1001", "**hello**")
    assert sent_calls[-1][0] == "sendMessage" and "parse_mode" not in sent_calls[-1][1]

    # Exercise live progress callbacks inside handle_telegram_update + no_id_client single profile & callback
    orig_exec = tg_bot_mod.execute_claim_task

    async def exec_calling_progress(db, **kwargs):
        cb = kwargs.get("on_progress")
        if cb is not None:
            await cb("step 1 progress")
        return await orig_exec(db, **kwargs)

    monkeypatch.setattr(tg_bot_mod, "execute_claim_task", exec_calling_progress)
    monkeypatch.setattr(maid_mod, "execute_claim_task", exec_calling_progress)
    assert await handle_telegram_update(
        db,
        update={"message": {"chat": {"id": 1001}, "photo": [{"file_id": "f-prog"}]}},
        bot_token="tok",
        allowed_chat_ids=["1001"],
        client=no_id_client,
        browser_runner=ok_browser,
    ) == "photo_handled"
    assert await handle_telegram_update(
        db,
        update={"callback_query": {"id": "cb-prog", "data": f"claim_profile:{task_id}:{p1.id}", "message": {"chat": {"id": 1001}, "message_id": 99, "text": "t"}}},
        bot_token="tok",
        allowed_chat_ids=["1001"],
        client=no_id_client,
        browser_runner=ok_browser,
    ) == "claim_submitted"
    assert await handle_telegram_update(
        db,
        update={"callback_query": {"id": "cb4", "data": "claim_profile:99999:99999", "message": {"chat": {"id": 1001}}}},
        bot_token="tok",
        allowed_chat_ids=["1001"],
        client=client,
    ) == "not_found"
    assert await handle_telegram_update(
        db,
        update={"callback_query": {"id": "cb5", "data": "other:1", "message": {"chat": {"id": 1001}}}},
        bot_token="tok",
        allowed_chat_ids=["1001"],
        client=client,
    ) == "ignored_callback"

    # Empty file_path download edge case
    class EmptyFileClient(FakeHttpClient):
        async def post(self, url: str, json: dict):
            if url.endswith("/getFile"):
                return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"result": {}})
            return await super().post(url, json)

    assert await tg_bot_mod._download_tg_file(EmptyFileClient(), "tok", "fid") == b""

    # No active admin edge case
    admin.is_active = False
    await db.commit()
    assert await handle_telegram_update(
        db, update={"message": {"chat": {"id": 1001}, "text": "hi"}}, bot_token="tok", allowed_chat_ids=[], client=client
    ) == "no_admin"


async def _coro(val):
    return val
