"""Multimodal Vision Browser Agent (Set-of-Mark) for QR invoice claiming.

Instead of relying on brittle hardcoded Chinese button labels ("发送邮箱", "立即开票"),
each step of the loop:
  1. Tags every visible interactive element on the page with a numeric badge
     (``data-miri-id="1"``, ``"2"``, ...) and extracts a compact element table.
  2. Sends the annotated screenshot + element table + saved InvoiceProfile +
     step history to the configured Vision LLM (e.g. ``auto/gemini-4-neon``).
  3. Executes the structured action decided by the VLM (``click``,
     ``fill_and_click``, ``done_submitted``, ``done_already_issued``, or
     ``needs_attention``), repeating up to ``max_steps`` turns.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import logging
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import InvoiceProfile

logger = logging.getLogger(__name__)

MAX_VISION_STEPS = 6

VISION_AGENT_SYSTEM_PROMPT = """你是发票助手米尔（Miri），正在无头手机浏览器中帮用户自动完成一张线下消费小票的开票（或已开具发票的邮箱重发/下载）。

你会看到：
1. 当前网页的实拍截图（页面上每个可交互元素左上角贴有红底白字编号 [1], [2], [3]...）；
2. 对应的可交互元素清单（含编号、标签、类型、placeholder、文本）；
3. 用户选定的预存发票抬头资料（抬头名称、税号、收票邮箱、手机号、地址、开户行及账号）；
4. 之前已执行过的步骤历史。

请根据画面与元素清单，选择最合理的下一步结构化动作：
- action="fill_and_click"：当前页有需要填写的输入框（如抬头、税号、邮箱、手机号、或用户刚提供的验证码），请在 fills 中一次性填齐所有可匹配的字段，并在 click_element_id 指定要点击的提交/发送/确认按钮（若只填不点可留空）。
- action="click"：当前页需要先点某个入口/开关/弹窗关闭按钮（例如「发送邮箱」「重发邮箱」「查看发票」「去开票」「企业/单位」「确定」「我知道了」等任何样式的按钮，无论中英文或什么 DOM 标签），在 click_element_id 填其编号。
- action="done_submitted"：画面已经明确表示开票申请提交成功，或电子发票已成功发送到收票邮箱！请在 message 里简明告知结果。
- action="needs_attention"：遇到无法自动完成的情况（如需要输入图形/短信验证码且用户尚未提供、页面提示「获取用户信息异常」/「请在微信中打开」/ 浏览器打开显示「404 + 微信小程序」（即微信“扫普通链接二维码打开小程序”规则）等微信客户端限制、二维码已过期/失效、收票邮箱缺失、或提交后出现报错）。请在 message 中用简洁人话说明页面真实状况与原因，若是微信限制或绑定微信小程序的普通链接请置 is_wechat_only=true。

重要规则：
- 严禁编造用户资料中不存在的税号或邮箱；如果页面要求填写邮箱而用户抬头里 email 为空，请返回 needs_attention 提示用户先补充收票邮箱。
- 若页面显示该订单早已开具发票（能看到发票号码），请先在 detected_invoice_no 中准确填出该 8 位或 20 位发票号码；如果页面有「发送邮箱」/「重发邮箱」按钮且用户抬头有邮箱，优先 click 该按钮把发票发到邮箱！
- 绝不要重复死循环点击同一个无效按钮；若上一步点击无变化，请换思路或返回 needs_attention。
- message 保持简洁直接、说人话，不要套话。"""


@dataclass
class InteractiveElement:
    id: int
    tag: str
    input_type: str = ""
    text: str = ""
    placeholder: str = ""
    label: str = ""
    value: str = ""


class FillInstruction(BaseModel):
    element_id: int = Field(description="Numeric element ID from the annotated screenshot / list.")
    value: str = Field(description="Exact text value to fill into this input.")


class BrowserAgentDecision(BaseModel):
    thought: str = Field(default="", description="Brief reasoning for this step.")
    action: Literal["click", "fill_and_click", "done_submitted", "needs_attention"] = Field(
        description="Next action to perform."
    )
    fills: list[FillInstruction] = Field(default_factory=list, description="Inputs to fill before clicking.")
    click_element_id: int | None = Field(default=None, description="Element ID to click.")
    detected_invoice_no: str = Field(default="", description="8- or 20-digit invoice number if already visible on page.")
    is_wechat_only: bool = Field(default=False, description="True if page requires WeChat in-app authorization/browser.")
    message: str = Field(default="", description="Concise Chinese summary for the user when finished or escalating.")


def format_elements_for_prompt(elements: list[InteractiveElement]) -> str:
    if not elements:
        return "(页面未检测到可见可交互元素)"
    lines: list[str] = []
    for el in elements[:60]:
        parts = [f"[{el.id}] <{el.tag}>"]
        if el.input_type:
            parts.append(f"type={el.input_type}")
        if el.label:
            parts.append(f'label="{el.label[:40]}"')
        if el.placeholder:
            parts.append(f'placeholder="{el.placeholder[:40]}"')
        if el.text:
            parts.append(f'text="{el.text[:40]}"')
        if el.value:
            parts.append(f'value="{el.value[:30]}"')
        lines.append(" ".join(parts))
    return "\n".join(lines)


def format_profile_for_prompt(profile: InvoiceProfile) -> str:
    return (
        f"抬头简称: {profile.name}\n"
        f"购方名称(抬头): {profile.title}\n"
        f"税号: {profile.tax_id or '(未填写)'}\n"
        f"发票类型偏好: {profile.invoice_type_pref or '数电普票'}\n"
        f"收票邮箱: {profile.email or '(未填写)'}\n"
        f"手机号: {profile.phone or '(未填写)'}\n"
        f"地址电话: {profile.address or '(未填写)'}\n"
        f"开户行: {profile.bank_name or '(未填写)'}\n"
        f"银行账号: {profile.bank_account or '(未填写)'}"
    )


async def decide_next_browser_action(
    db: AsyncSession,
    ai: Any,
    *,
    screenshot_bytes: bytes,
    url: str,
    page_text: str,
    elements: list[InteractiveElement],
    profile: InvoiceProfile,
    history: list[str],
    captcha_input: str = "",
) -> BrowserAgentDecision | None:
    """Ask the multimodal VLM to inspect the annotated screenshot and choose the next browser action."""
    if ai is None or not screenshot_bytes or not hasattr(ai, "_create_structured_with_fallback"):
        return None
    try:
        runtime_settings = await ai._resolve_runtime_settings(db)
        vision_model = str(runtime_settings.get("llm_vision_model", "")).strip()
        img_b64 = base64.b64encode(screenshot_bytes).decode("ascii")
        history_block = "\n".join(f"{i + 1}. {h}" for i, h in enumerate(history)) or "(尚未执行操作)"
        captcha_line = f"\n用户刚提供的验证码：{captcha_input}" if captcha_input else ""
        user_text = (
            f"当前网址: {url}\n\n"
            f"=== 预存发票抬头 ===\n{format_profile_for_prompt(profile)}{captcha_line}\n\n"
            f"=== 已执行步骤 ===\n{history_block}\n\n"
            f"=== 页面可见文本节选 ===\n{(page_text or '')[:1200]}\n\n"
            f"=== 可交互元素清单（与截图红标编号一一对应） ===\n{format_elements_for_prompt(elements)}"
        )
        return await ai._create_structured_with_fallback(
            runtime_settings=runtime_settings,
            response_model=BrowserAgentDecision,
            preferred_first_model=vision_model,
            messages=[
                {"role": "system", "content": VISION_AGENT_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                    ],
                },
            ],
        )
    except Exception as exc:
        logger.warning("Vision browser agent decision failed: %s", exc)
        return None


_SOM_TAG_SCRIPT = """() => {
    document.querySelectorAll('.__miri_som_badge').forEach(n => n.remove());
    document.querySelectorAll('[data-miri-id]').forEach(n => n.removeAttribute('data-miri-id'));

    const isVisible = (el) => {
        const rect = el.getBoundingClientRect();
        if (rect.width < 8 || rect.height < 8) return false;
        const style = window.getComputedStyle(el);
        return style.display !== 'none' && style.visibility !== 'hidden' && parseFloat(style.opacity || '1') > 0;
    };

    const candidates = [];
    const seen = new Set();

    const addEl = (el) => {
        if (seen.has(el) || !isVisible(el)) return;
        seen.add(el);
        candidates.push(el);
    };

    // 1. Native interactive controls
    for (const el of document.querySelectorAll('input, textarea, select, button, a, [role="button"], [role="tab"]')) {
        if (el.disabled) continue;
        addEl(el);
    }

    // 2. Mobile H5 div/span pseudo-buttons (pointer cursor, btn class, or compact leaf text)
    for (const el of document.querySelectorAll('div, span, li, p')) {
        if (seen.has(el) || !isVisible(el)) continue;
        const rect = el.getBoundingClientRect();
        if (rect.width > 420 || rect.height > 90) continue;
        const style = window.getComputedStyle(el);
        const cls = String(el.className || '').toLowerCase();
        const text = (el.innerText || '').trim();
        const looksBtn = style.cursor === 'pointer' || /btn|button|submit|tab|radio|check|action|confirm|send|item/.test(cls);
        const hasChildInteractive = el.querySelector('input, textarea, button, a');
        if (looksBtn && !hasChildInteractive && text.length > 0 && text.length <= 28) {
            addEl(el);
        }
    }

    const out = [];
    let idx = 0;
    for (const el of candidates.slice(0, 60)) {
        idx += 1;
        el.setAttribute('data-miri-id', String(idx));
        const rect = el.getBoundingClientRect();

        const badge = document.createElement('div');
        badge.className = '__miri_som_badge';
        badge.textContent = String(idx);
        badge.style.cssText = [
            'position:fixed',
            `left:${Math.max(2, Math.round(rect.left))}px`,
            `top:${Math.max(2, Math.round(rect.top))}px`,
            'z-index:2147483647',
            'background:#D93829',
            'color:#FFFFFF',
            'font:bold 11px/14px monospace',
            'padding:1px 4px',
            'border-radius:3px',
            'pointer-events:none',
            'box-shadow:0 1px 2px rgba(0,0,0,0.4)',
        ].join(';');
        document.body.appendChild(badge);

        const labelEl = el.id ? document.querySelector(`label[for="${el.id}"]`) : el.closest('label');
        const parentText = el.parentElement ? (el.parentElement.innerText || '').trim().slice(0, 35) : '';
        out.push({
            id: idx,
            tag: el.tagName.toLowerCase(),
            input_type: (el.getAttribute('type') || '').toLowerCase(),
            text: (el.innerText || '').trim().slice(0, 40),
            placeholder: (el.getAttribute('placeholder') || '').trim().slice(0, 40),
            label: ((labelEl ? labelEl.innerText.trim() : '') || (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' ? parentText : '')).slice(0, 40),
            value: (el.value || '').toString().slice(0, 30),
        });
    }
    return out;
}"""

_SOM_CLEAR_SCRIPT = """() => {
    document.querySelectorAll('.__miri_som_badge').forEach(n => n.remove());
}"""


async def _emit_progress(on_progress: Callable[[str], Awaitable[None]] | None, text: str) -> None:  # pragma: no cover - real browser progress callback
    if on_progress is None:
        return
    try:
        await on_progress(text)
    except Exception:
        pass


async def run_vision_claim_loop(  # pragma: no cover - real Playwright execution path
    page: Any,
    *,
    db: AsyncSession,
    ai: Any,
    profile: InvoiceProfile,
    captcha_input: str = "",
    max_steps: int = MAX_VISION_STEPS,
    on_progress: Callable[[str], Awaitable[None]] | None = None,
) -> dict[str, Any] | None:
    """Drive a live Playwright ``page`` using the Set-of-Mark Vision Agent Loop.

    Returns a structured outcome dict compatible with ``BrowserClaimOutcome``,
    or ``None`` if VLM is unavailable so the caller can fall back cleanly.
    """
    steps_taken: list[str] = ["打开开票链接"]
    sticky_invoice_no = ""

    for step in range(max_steps):
        await _emit_progress(on_progress, f"👀 第 {step + 1} 步：正在查看页面内容并分析下一步…")
        raw_elements = await page.evaluate(_SOM_TAG_SCRIPT)
        elements = [InteractiveElement(**item) for item in raw_elements]
        annotated_shot = await page.screenshot(full_page=True)
        await page.evaluate(_SOM_CLEAR_SCRIPT)
        body_text = (await page.inner_text("body")) or ""

        decision = await decide_next_browser_action(
            db,
            ai,
            screenshot_bytes=annotated_shot,
            url=str(page.url),
            page_text=body_text,
            elements=elements,
            profile=profile,
            history=steps_taken,
            captcha_input=captcha_input if step == 0 else "",
        )
        if decision is None:
            return None

        if decision.detected_invoice_no and not sticky_invoice_no:
            sticky_invoice_no = decision.detected_invoice_no.strip()

        if decision.action == "done_submitted":
            return {
                "kind": "submitted",
                "detail": decision.message or "已自动完成开票/重发提交。",
                "screenshot_bytes": None,
                "detected_invoice_no": sticky_invoice_no,
                "page_text_excerpt": body_text[:240],
                "steps_taken": steps_taken,
            }

        if decision.action == "needs_attention":
            clean_shot = await page.screenshot(full_page=True)
            return {
                "kind": "needs_attention",
                "detail": decision.message or "页面需要人工确认，请查看截图。",
                "screenshot_bytes": clean_shot,
                "detected_invoice_no": sticky_invoice_no,
                "page_text_excerpt": body_text[:240],
                "steps_taken": steps_taken,
                "is_wechat_only": decision.is_wechat_only,
            }

        # Execute fill_and_click or click
        action_notes: list[str] = []
        if decision.fills:
            filled_count = 0
            for f in decision.fills:
                loc = page.locator(f'[data-miri-id="{f.element_id}"]').first
                if await loc.count() > 0:
                    await loc.fill(f.value)
                    filled_count += 1
            if filled_count:
                note = f"填写 {filled_count} 个字段"
                steps_taken.append(note)
                action_notes.append(note)

        if decision.click_element_id is not None:
            click_loc = page.locator(f'[data-miri-id="{decision.click_element_id}"]').first
            if await click_loc.count() > 0:
                clicked_label = ((await click_loc.inner_text()) or f"#{decision.click_element_id}").strip()[:20]
                await click_loc.click()
                note = f"点击「{clicked_label}」"
                steps_taken.append(note)
                action_notes.append(note)
                await _emit_progress(on_progress, f"⚡ 第 {step + 1} 步：{'并'.join(action_notes)}，等待页面响应…")
                await page.wait_for_timeout(2500)
            else:
                steps_taken.append(f"元素 #{decision.click_element_id} 未找到")
        elif action_notes:
            await _emit_progress(on_progress, f"✍️ 第 {step + 1} 步：{'、'.join(action_notes)}…")

    clean_shot = await page.screenshot(full_page=True)
    final_text = (await page.inner_text("body")) or ""
    return {
        "kind": "needs_attention",
        "detail": f"尝试了 {max_steps} 步仍未确认提交完成，请查看最新页面截图。\n已执行：{' → '.join(steps_taken)}",
        "screenshot_bytes": clean_shot,
        "detected_invoice_no": sticky_invoice_no,
        "page_text_excerpt": final_text[:240],
        "steps_taken": steps_taken,
    }
