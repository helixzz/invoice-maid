from __future__ import annotations

import pytest

from app.models import InvoiceProfile
from app.services.vision_browser_agent import (
    BrowserAgentDecision,
    FillInstruction,
    InteractiveElement,
    decide_next_browser_action,
    format_elements_for_prompt,
    format_profile_for_prompt,
)


def _profile() -> InvoiceProfile:
    return InvoiceProfile(
        id=1,
        user_id=1,
        name="佳期私募",
        title="上海佳期私募基金管理有限公司",
        tax_id="91310000123456789X",
        invoice_type_pref="数电普票",
        email="inv@company.com",
        phone="13800138000",
        address="",
        bank_name="",
        bank_account="",
    )


def test_format_helpers_cover_empty_and_populated_fields() -> None:
    assert "未检测到" in format_elements_for_prompt([])
    rendered = format_elements_for_prompt(
        [
            InteractiveElement(
                id=1,
                tag="input",
                input_type="email",
                label="电子邮箱",
                placeholder="请输入邮箱地址",
                text="",
                value="a@b.com",
            ),
            InteractiveElement(id=2, tag="div", text="发送邮箱"),
        ]
    )
    assert "[1] <input> type=email" in rendered
    assert 'placeholder="请输入邮箱地址"' in rendered
    assert 'value="a@b.com"' in rendered
    assert '[2] <div> text="发送邮箱"' in rendered

    prof_str = format_profile_for_prompt(_profile())
    assert "上海佳期私募基金管理有限公司" in prof_str
    assert "inv@company.com" in prof_str


@pytest.mark.asyncio
async def test_decide_next_browser_action_success_and_error_fallbacks(db) -> None:
    prof = _profile()
    # Missing AI or empty screenshot returns None cleanly
    assert (
        await decide_next_browser_action(
            db,
            None,
            screenshot_bytes=b"png",
            url="https://x",
            page_text="",
            elements=[],
            profile=prof,
            history=[],
        )
        is None
    )
    assert (
        await decide_next_browser_action(
            db,
            object(),
            screenshot_bytes=b"",
            url="https://x",
            page_text="",
            elements=[],
            profile=prof,
            history=[],
        )
        is None
    )

    captured_messages: list[dict] = []

    class FakeVlmAI:
        async def _resolve_runtime_settings(self, _db):
            return {"llm_vision_model": "auto/gemini-4-neon"}

        async def _create_structured_with_fallback(self, **kwargs):
            captured_messages.extend(kwargs["messages"])
            return BrowserAgentDecision(
                thought="页面显示已开具发票，点击底部[2]发送邮箱按钮",
                action="click",
                click_element_id=2,
                detected_invoice_no="26312000006229050871",
            )

    decision = await decide_next_browser_action(
        db,
        FakeVlmAI(),
        screenshot_bytes=b"\x89PNGfake",
        url="https://invoice.meituan.com/invoice.html#/eInvoice",
        page_text="发票号码 26312000006229050871 发送邮箱",
        elements=[InteractiveElement(id=2, tag="div", text="发送邮箱")],
        profile=prof,
        history=["打开开票链接"],
        captcha_input="9988",
    )
    assert decision is not None
    assert decision.action == "click"
    assert decision.click_element_id == 2
    assert decision.detected_invoice_no == "26312000006229050871"
    user_text = captured_messages[1]["content"][0]["text"]
    assert "9988" in user_text
    assert "发送邮箱" in user_text

    # FillInstruction + fill_and_click schema sanity
    fill_dec = BrowserAgentDecision(
        action="fill_and_click",
        fills=[FillInstruction(element_id=1, value="inv@company.com")],
        click_element_id=3,
    )
    assert fill_dec.fills[0].element_id == 1

    # Exception in VLM returns None cleanly so caller falls back
    class BrokenVlmAI(FakeVlmAI):
        async def _create_structured_with_fallback(self, **kwargs):
            del kwargs
            raise RuntimeError("vision error")

    assert (
        await decide_next_browser_action(
            db,
            BrokenVlmAI(),
            screenshot_bytes=b"\x89PNGfake",
            url="https://x",
            page_text="err",
            elements=[],
            profile=prof,
            history=[],
        )
        is None
    )
