"""Multi-step Playwright + VLM form-filling agent for QR invoice claim tasks.

Policy:
  - Auto-fill and auto-submit without asking confirmation on normal forms.
  - Perform multi-step exploration (click entry buttons, handle already-issued
    invoices by checking DB or triggering "发送邮箱" / direct PDF download).
  - Escalate to ``needs_attention`` ONLY when something unexpected occurs,
    always capturing a screenshot, recording steps attempted, and producing a
    clear human-readable diagnosis (including WeChat-auth detection + copyable
    header card + page text excerpt / VLM visual summary).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
from pathlib import Path
import re
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Invoice, InvoiceClaimTask, InvoiceProfile
from app.models.invoice import invoice_no_dedup_expression
from app.services.invoice_parser import invoice_no_dedup_key
from app.services.qr_receipt_decoder import is_wechat_only_url

logger = logging.getLogger(__name__)

SUCCESS_TEXT_SIGNALS = (
    "开票成功",
    "申请成功",
    "已提交",
    "提交成功",
    "开具中",
    "开票申请已受理",
    "发送至您的邮箱",
    "发送到邮箱",
    "发送成功",
    "已发送至邮箱",
    "invoice submitted",
    "successfully submitted",
)

ERROR_TEXT_SIGNALS = (
    "验证码错误",
    "格式不正确",
    "不能为空",
    "开票失败",
    "获取用户信息异常",
    "超出开票期限",
    "已过开票期限",
    "二维码已失效",
    "请在微信客户端打开",
    "请在微信中打开",
    "系统繁忙",
)

WECHAT_AUTH_TEXT_SIGNALS = (
    "请在微信客户端打开",
    "请在微信中打开",
    "请在微信内打开",
    "获取用户信息异常",
    "微信授权失败",
    "请使用微信扫码",
    "仅支持微信",
    "微信小程序",
)

WECHAT_URL_PATH_HINTS = ("/fpwx/", "/wx/", "wxinvoice", "weixin")

CAPTCHA_FIELD_HINTS = ("验证码", "captcha", "verify", "vcode", "图形码", "校验码")

ENTRY_BUTTON_TEXTS = (
    "发送邮箱",
    "重发邮箱",
    "立即开票",
    "去开票",
    "申请开票",
    "开发票",
)

SUBMIT_BUTTON_TEXTS = (
    "确认开票",
    "提交开票",
    "申请开票",
    "立即开票",
    "确认发送",
    "提交",
    "发送",
    "确定",
    "确认",
)

_INVOICE_NO_RE = re.compile(r"发票号码[：:\s]*(\d{20}|\d{8})\b")


class PageDiagnosis(BaseModel):
    summary: str = Field(description="2-sentence plain Chinese explanation of what the page shows and why auto-claiming stopped.")


@dataclass
class FormFieldInfo:
    selector: str
    label: str = ""
    placeholder: str = ""
    name: str = ""
    field_type: str = "text"


@dataclass
class BrowserClaimOutcome:
    """Result returned by the browser executor after attempting a claim URL."""

    kind: str  # "submitted" | "pdf_downloaded" | "already_issued" | "needs_attention" | "failed"
    detail: str
    screenshot_bytes: bytes | None = None
    pdf_bytes: bytes | None = None
    pdf_filename: str = "claimed_invoice.pdf"
    detected_invoice_no: str = ""
    page_text_excerpt: str = ""
    steps_taken: list[str] = field(default_factory=list)


BrowserRunner = Callable[
    [str, InvoiceProfile, str],
    Awaitable[BrowserClaimOutcome],
]


def append_claim_log(task: InvoiceClaimTask, event: str, detail: str = "") -> None:
    try:
        entries = json.loads(task.log_json or "[]")
        if not isinstance(entries, list):
            entries = []
    except (TypeError, ValueError):
        entries = []
    entries.append(
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "detail": detail[:500],
        }
    )
    task.log_json = json.dumps(entries[-50:], ensure_ascii=False)


def format_copyable_profile_card(profile: InvoiceProfile) -> str:
    """Plain-text header card for quick copy-paste when a QR code needs manual/WeChat handling."""
    lines = [f"抬头名称：{profile.title}"]
    if profile.tax_id:
        lines.append(f"税号：{profile.tax_id}")
    if profile.email:
        lines.append(f"收票邮箱：{profile.email}")
    if profile.phone:
        lines.append(f"手机号：{profile.phone}")
    if profile.address:
        lines.append(f"地址电话：{profile.address}")
    if profile.bank_name or profile.bank_account:
        lines.append(f"开户行及账号：{profile.bank_name} {profile.bank_account}".strip())
    return "\n".join(lines)


def extract_issued_invoice_no(page_text: str) -> str:
    """Extract an 8- or 20-digit invoice number when a page displays an already-issued invoice."""
    if not page_text:
        return ""
    match = _INVOICE_NO_RE.search(page_text)
    return match.group(1) if match else ""


def is_wechat_auth_blocked(url: str, page_text: str) -> bool:
    """True when the page requires a WeChat in-app browser session (e.g. /fpwx/ OpenID error)."""
    lowered_text = (page_text or "").lower()
    if any(sig in lowered_text for sig in WECHAT_AUTH_TEXT_SIGNALS):
        return True
    lowered_url = (url or "").lower()
    if any(hint in lowered_url for hint in WECHAT_URL_PATH_HINTS) and "失败" in lowered_text:
        return True
    return False


def build_page_excerpt(page_text: str, limit: int = 240) -> str:
    cleaned = re.sub(r"\s+", " ", (page_text or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit] + "…"


def diagnose_non_form_page(
    *,
    url: str,
    page_text: str,
    profile: InvoiceProfile,
    steps_taken: list[str] | None = None,
) -> tuple[str, str]:
    """Produce a clear, actionable diagnosis when no standard fillable form succeeded.

    Returns ``(kind, detailed_message)``.
    """
    steps = steps_taken or []
    excerpt = build_page_excerpt(page_text, limit=220)
    steps_line = f"\n已尝试步骤：{' → '.join(steps)}" if steps else ""

    if is_wechat_auth_blocked(url, page_text):
        msg = (
            "该开票页面依赖微信客户端内登录/授权，外部浏览器无法获取微信用户信息。"
            + (f"\n页面提示：{excerpt}" if excerpt else "")
            + steps_line
            + "\n\n请用微信扫原小票二维码，并直接复制以下抬头信息：\n"
            + format_copyable_profile_card(profile)
        )
        return "needs_attention", msg

    issued_no = extract_issued_invoice_no(page_text)
    if issued_no:
        msg = (
            f"该订单页面显示发票已开具（发票号码：{issued_no}），未能自动触发下载或重发邮箱。"
            + (f"\n页面内容：{excerpt}" if excerpt else "")
            + steps_line
            + "\n请查看下方页面截图，可直接在页面点击「查看发票」或「发送邮箱」。"
        )
        return "already_issued", msg

    for err in ERROR_TEXT_SIGNALS:
        if err in (page_text or ""):
            msg = (
                f"开票页面返回提示：{err}"
                + (f"\n页面原文：{excerpt}" if excerpt else "")
                + steps_line
            )
            return "needs_attention", msg

    if excerpt:
        msg = (
            f"未能完成自动填表。当前页面显示：「{excerpt}」"
            + steps_line
            + "\n请查看下方截图确认页面情况。"
        )
    else:
        msg = (
            "打开开票链接后页面为空白或未加载出可填写的表单字段。"
            + steps_line
            + "\n请查看下方截图确认或手工开票。"
        )
    return "needs_attention", msg


def plan_deterministic_fills(
    fields: list[FormFieldInfo],
    profile: InvoiceProfile,
    captcha_input: str = "",
) -> tuple[list[tuple[str, str]], str | None]:
    """Map visible form inputs to ``profile`` fields.

    Returns ``(fills, attention_reason)``. If a CAPTCHA field is detected and
    ``captcha_input`` was not provided, returns an attention reason immediately.
    """
    fills: list[tuple[str, str]] = []
    for fld in fields:
        if fld.field_type in ("hidden", "submit", "button", "checkbox", "radio"):
            continue
        blob = f"{fld.label} {fld.placeholder} {fld.name}".lower()
        if any(h in blob for h in CAPTCHA_FIELD_HINTS):
            if captcha_input.strip():
                fills.append((fld.selector, captcha_input.strip()))
            else:
                return fills, "开票页面需要输入图形/短信验证码，请查看截图后回复验证码继续。"
            continue
        if any(k in blob for k in ("抬头", "购买方", "购方名称", "公司名称", "企业名称", "title", "buyer")):
            if profile.title:
                fills.append((fld.selector, profile.title))
        elif any(k in blob for k in ("税号", "纳税人", "信用代码", "tax", "tin")):
            if profile.tax_id:
                fills.append((fld.selector, profile.tax_id))
        elif any(k in blob for k in ("邮箱", "email", "mail")):
            if profile.email:
                fills.append((fld.selector, profile.email))
        elif any(k in blob for k in ("手机", "电话", "phone", "mobile", "tel")):
            if profile.phone:
                fills.append((fld.selector, profile.phone))
        elif any(k in blob for k in ("地址", "address")):
            if profile.address:
                fills.append((fld.selector, profile.address))
        elif any(k in blob for k in ("开户行", "银行名称", "bank_name")):
            if profile.bank_name:
                fills.append((fld.selector, profile.bank_name))
        elif any(k in blob for k in ("银行账号", "账号", "bank_account")):
            if profile.bank_account:
                fills.append((fld.selector, profile.bank_account))
    return fills, None


def evaluate_post_submit_text(page_text: str) -> tuple[str, str]:
    """Classify post-submit page text into ``('submitted' | 'needs_attention', detail)``."""
    lowered = (page_text or "").lower()
    for err in ERROR_TEXT_SIGNALS:
        if err.lower() in lowered:
            return "needs_attention", f"开票页面提示异常：{err}"
    for ok in SUCCESS_TEXT_SIGNALS:
        if ok.lower() in lowered:
            return "submitted", f"已自动提交开票申请（页面反馈：{ok}）"
    excerpt = build_page_excerpt(page_text, limit=180)
    return (
        "needs_attention",
        f"已点击提交但未看到明确成功提示。当前页面内容：「{excerpt}」" if excerpt else "已点击提交但未能确认开票成功提示，请核对截图。",
    )


async def _extract_visible_fields(page: Any) -> list[FormFieldInfo]:  # pragma: no cover - real browser DOM eval
    raw_fields = await page.evaluate(
        """() => {
            const out = [];
            const els = document.querySelectorAll('input, textarea');
            let idx = 0;
            for (const el of els) {
                if (el.offsetParent === null || el.disabled || el.readOnly) continue;
                idx += 1;
                el.setAttribute('data-maid-idx', String(idx));
                const labelEl = el.id ? document.querySelector(`label[for="${el.id}"]`) : el.closest('label');
                const parentText = el.parentElement ? (el.parentElement.innerText || '').trim().slice(0, 40) : '';
                out.push({
                    selector: `[data-maid-idx="${idx}"]`,
                    label: (labelEl ? labelEl.innerText.trim() : '') || parentText,
                    placeholder: el.getAttribute('placeholder') || '',
                    name: el.getAttribute('name') || el.id || '',
                    field_type: (el.getAttribute('type') || 'text').toLowerCase(),
                });
            }
            return out;
        }"""
    )
    return [FormFieldInfo(**f) for f in raw_fields]


async def _find_clickable_by_texts(page: Any, labels: tuple[str, ...]) -> tuple[Any | None, str]:  # pragma: no cover - real browser helper
    """Locate a visible clickable element matching any label regardless of tag (button/a/div/span)."""
    for label in labels:
        for loc in (
            page.locator(f'button:has-text("{label}"), a:has-text("{label}")').first,
            page.get_by_text(label, exact=True).last,
        ):
            try:
                if await loc.count() > 0 and await loc.is_visible():
                    return loc, label
            except Exception:
                continue
    return None, ""


async def _default_playwright_runner(  # pragma: no cover - real headless browser path
    qr_url: str,
    profile: InvoiceProfile,
    captcha_input: str = "",
    *,
    db: AsyncSession | None = None,
    ai: Any = None,
    on_progress: Callable[[str], Awaitable[None]] | None = None,
) -> BrowserClaimOutcome:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return BrowserClaimOutcome(
            kind="needs_attention",
            detail="服务器未安装 Playwright 浏览器，请手工开票。",
        )

    steps_taken: list[str] = []
    sticky_issued_no = ""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 430, "height": 900},
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
            ),
        )
        page = await context.new_page()
        try:
            if on_progress is not None:
                try:
                    await on_progress("🌐 正在打开开票网页并等待加载…")
                except Exception:
                    pass
            resp = await page.goto(qr_url, wait_until="load", timeout=30_000)
            steps_taken.append("打开开票链接")
            if resp is not None:
                ctype = (resp.headers.get("content-type") or "").lower()
                if "application/pdf" in ctype:
                    pdf_bytes = await resp.body()
                    return BrowserClaimOutcome(
                        kind="pdf_downloaded",
                        detail="开票链接直接返回 PDF 发票",
                        pdf_bytes=pdf_bytes,
                        steps_taken=steps_taken,
                    )

            # Give SPA frameworks (Vue/React, e.g. Meituan eInvoice) time to hydrate
            await page.wait_for_timeout(2500)

            # Primary intelligent path: Multimodal Vision Browser Agent Loop (Set-of-Mark)
            if db is not None and ai is not None:
                from app.services.vision_browser_agent import run_vision_claim_loop

                vision_res = await run_vision_claim_loop(
                    page,
                    db=db,
                    ai=ai,
                    profile=profile,
                    captcha_input=captcha_input,
                    on_progress=on_progress,
                )
                if vision_res is not None:
                    detail = str(vision_res.get("detail") or "")
                    if vision_res.get("is_wechat_only") and profile.title not in detail:
                        detail = f"{detail}\n\n请用微信扫原小票二维码，并复制以下抬头：\n{format_copyable_profile_card(profile)}"
                    steps = vision_res.get("steps_taken") or steps_taken
                    if vision_res.get("kind") == "needs_attention" and steps and "已执行" not in detail:
                        detail = f"{detail}\n已执行步骤：{' → '.join(steps)}"
                    return BrowserClaimOutcome(
                        kind=str(vision_res.get("kind") or "needs_attention"),
                        detail=detail,
                        screenshot_bytes=vision_res.get("screenshot_bytes"),
                        detected_invoice_no=str(vision_res.get("detected_invoice_no") or ""),
                        page_text_excerpt=str(vision_res.get("page_text_excerpt") or ""),
                        steps_taken=steps,
                    )

            for step_idx in range(3):
                body_text = (await page.inner_text("body")) or ""
                issued_no = extract_issued_invoice_no(body_text)
                if issued_no and not sticky_issued_no:
                    sticky_issued_no = issued_no

                if is_wechat_auth_blocked(page.url, body_text):
                    shot = await page.screenshot(full_page=True)
                    kind, detail = diagnose_non_form_page(
                        url=page.url,
                        page_text=body_text,
                        profile=profile,
                        steps_taken=steps_taken,
                    )
                    return BrowserClaimOutcome(
                        kind=kind,
                        detail=detail,
                        screenshot_bytes=shot,
                        page_text_excerpt=build_page_excerpt(body_text),
                        steps_taken=steps_taken,
                    )

                fields = await _extract_visible_fields(page)
                fills, attention = plan_deterministic_fills(fields, profile, captcha_input=captcha_input)
                if attention:
                    shot = await page.screenshot(full_page=True)
                    return BrowserClaimOutcome(
                        kind="needs_attention",
                        detail=attention,
                        screenshot_bytes=shot,
                        detected_invoice_no=sticky_issued_no,
                        page_text_excerpt=build_page_excerpt(body_text),
                        steps_taken=steps_taken,
                    )

                if fills:
                    for selector, value in fills:
                        await page.locator(selector).first.fill(value)
                    steps_taken.append(f"填写 {len(fills)} 个表单字段")

                    submit_btn, submit_label = await _find_clickable_by_texts(page, SUBMIT_BUTTON_TEXTS)
                    if submit_btn is None:
                        fallback_btn = page.locator('button[type="submit"]').first
                        if await fallback_btn.count() > 0:
                            submit_btn = fallback_btn
                            submit_label = "submit"
                    if submit_btn is None:
                        shot = await page.screenshot(full_page=True)
                        return BrowserClaimOutcome(
                            kind="needs_attention",
                            detail="表单已填写但未找到提交按钮，请查看截图确认。",
                            screenshot_bytes=shot,
                            detected_invoice_no=sticky_issued_no,
                            page_text_excerpt=build_page_excerpt(body_text),
                            steps_taken=steps_taken,
                        )

                    await submit_btn.click()
                    steps_taken.append(f"点击「{submit_label}」")
                    await page.wait_for_timeout(3000)
                    post_text = (await page.inner_text("body")) or ""
                    status_kind, detail = evaluate_post_submit_text(post_text)
                    shot = await page.screenshot(full_page=True) if status_kind != "submitted" else None
                    final_no = sticky_issued_no or extract_issued_invoice_no(post_text)
                    if final_no and status_kind == "submitted":
                        detail = f"该订单已开具发票（号码 {final_no}），已自动重发至收票邮箱（{profile.email}）！"
                    return BrowserClaimOutcome(
                        kind=status_kind,
                        detail=detail,
                        screenshot_bytes=shot,
                        detected_invoice_no=final_no,
                        page_text_excerpt=build_page_excerpt(post_text),
                        steps_taken=steps_taken,
                    )

                # No visible input fields yet -> look for any visible entry / resend-email control (div/span/button/a)
                clicked_entry = False
                if step_idx < 2:
                    entry_loc, entry_label = await _find_clickable_by_texts(page, ENTRY_BUTTON_TEXTS)
                    if entry_loc is not None:
                        await entry_loc.click()
                        steps_taken.append(f"点击「{entry_label}」")
                        await page.wait_for_timeout(2000)
                        clicked_entry = True
                if not clicked_entry:
                    shot = await page.screenshot(full_page=True)
                    kind, detail = diagnose_non_form_page(
                        url=page.url,
                        page_text=body_text,
                        profile=profile,
                        steps_taken=steps_taken,
                    )
                    return BrowserClaimOutcome(
                        kind=kind,
                        detail=detail,
                        screenshot_bytes=shot,
                        detected_invoice_no=sticky_issued_no,
                        page_text_excerpt=build_page_excerpt(body_text),
                        steps_taken=steps_taken,
                    )

            final_text = (await page.inner_text("body")) or ""
            shot = await page.screenshot(full_page=True)
            kind, detail = diagnose_non_form_page(
                url=page.url,
                page_text=final_text,
                profile=profile,
                steps_taken=steps_taken,
            )
            return BrowserClaimOutcome(
                kind=kind,
                detail=detail,
                screenshot_bytes=shot,
                detected_invoice_no=sticky_issued_no or extract_issued_invoice_no(final_text),
                page_text_excerpt=build_page_excerpt(final_text),
                steps_taken=steps_taken,
            )
        except Exception as exc:
            try:
                shot = await page.screenshot(full_page=True)
            except Exception:
                shot = None
            return BrowserClaimOutcome(
                kind="needs_attention",
                detail=f"自动填表遇到异常：{exc}"[:500],
                screenshot_bytes=shot,
                steps_taken=steps_taken,
            )
        finally:
            await browser.close()


async def _enrich_diagnosis_with_vlm(
    db: AsyncSession,
    ai: Any,
    screenshot_bytes: bytes | None,
    fallback_detail: str,
) -> str:
    """Ask VLM to briefly explain an unrecognized failure screenshot when available."""
    if not screenshot_bytes or ai is None or not hasattr(ai, "_create_structured_with_fallback"):
        return fallback_detail
    try:
        runtime_settings = await ai._resolve_runtime_settings(db)
        img_b64 = base64.b64encode(screenshot_bytes).decode("ascii")
        vision_model = str(runtime_settings.get("llm_vision_model", "")).strip()
        diag: PageDiagnosis = await ai._create_structured_with_fallback(
            runtime_settings=runtime_settings,
            response_model=PageDiagnosis,
            preferred_first_model=vision_model,
            messages=[
                {
                    "role": "system",
                    "content": "你是发票助手米尔，请用1-2句简洁中文描述这张开票网页截图里显示的真实内容或报错，不加套话。",
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "描述截图里的关键页面状态和报错信息。"},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                    ],
                },
            ],
        )
        summary = (diag.summary or "").strip()
        if summary and summary not in fallback_detail:
            return f"{summary}\n\n{fallback_detail}"
    except Exception as exc:
        logger.warning("VLM screenshot diagnosis skipped: %s", exc)
    return fallback_detail


def _save_claim_screenshot(storage_path: str, user_id: int, task_id: int, png_bytes: bytes) -> str:
    target_dir = Path(storage_path).expanduser() / "users" / str(user_id) / "claims"
    target_dir.mkdir(parents=True, exist_ok=True)
    rel = f"users/{user_id}/claims/claim_{task_id}.png"
    (Path(storage_path).expanduser() / rel).write_bytes(png_bytes)
    return rel


async def execute_claim_task(
    db: AsyncSession,
    *,
    task: InvoiceClaimTask,
    profile: InvoiceProfile,
    settings: Settings,
    ai: Any = None,
    file_mgr: Any = None,
    captcha_input: str = "",
    browser_runner: BrowserRunner | None = None,
    on_progress: Callable[[str], Awaitable[None]] | None = None,
) -> InvoiceClaimTask:
    """Execute one attempt of a QR invoice claim task with multi-step exploration & escalation."""
    now = datetime.now(timezone.utc)
    task.profile_id = profile.id
    task.attempts += 1
    task.updated_at = now

    if not task.qr_url:
        task.status = "needs_attention"
        task.attention_reason = "未能从照片中解析出开票二维码链接，请重拍更清晰的二维码或直接粘贴开票链接。"
        append_claim_log(task, "needs_attention", task.attention_reason)
        await db.commit()
        return task

    if is_wechat_only_url(task.qr_url):
        task.status = "needs_attention"
        task.attention_reason = (
            "该二维码为微信客户端限定链接，浏览器无法直接打开。请微信扫码并复制以下抬头：\n"
            + format_copyable_profile_card(profile)
        )
        append_claim_log(task, "wechat_only", "Escalated with copyable profile card")
        await db.commit()
        return task

    task.status = "filling"
    append_claim_log(task, "filling", f"Using profile '{profile.name}' ({profile.title})")
    await db.commit()

    if browser_runner is not None:
        outcome = await browser_runner(task.qr_url, profile, captcha_input)
    else:  # pragma: no cover - real headless browser path
        outcome = await _default_playwright_runner(
            task.qr_url,
            profile,
            captcha_input,
            db=db,
            ai=ai,
            on_progress=on_progress,
        )

    if outcome.screenshot_bytes:
        task.last_screenshot_path = _save_claim_screenshot(
            settings.STORAGE_PATH, task.user_id, task.id, outcome.screenshot_bytes
        )

    # Check if the page revealed an already-issued invoice number that is already archived in DB
    if outcome.detected_invoice_no:
        existing = (
            await db.execute(
                select(Invoice).where(
                    Invoice.user_id == task.user_id,
                    invoice_no_dedup_expression() == invoice_no_dedup_key(outcome.detected_invoice_no),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            task.matched_invoice_id = existing.id
            task.status = "completed"
            task.attention_reason = ""
            append_claim_log(
                task,
                "completed",
                f"Invoice {existing.invoice_no} already archived as #{existing.id}",
            )
            task.updated_at = datetime.now(timezone.utc)
            await db.commit()
            return task

    if outcome.kind == "pdf_downloaded" and outcome.pdf_bytes and ai is not None and file_mgr is not None:
        from app.services.manual_upload import process_uploaded_invoice

        upload_res = await process_uploaded_invoice(
            db=db,
            ai=ai,
            file_mgr=file_mgr,
            settings=settings,
            filename=outcome.pdf_filename,
            payload=outcome.pdf_bytes,
            user_id=task.user_id,
        )
        if upload_res.invoice is not None:
            task.matched_invoice_id = upload_res.invoice.id
            task.status = "completed"
            task.attention_reason = ""
            append_claim_log(task, "completed", f"Direct PDF saved as invoice #{upload_res.invoice.id}")
            task.updated_at = datetime.now(timezone.utc)
            await db.commit()
            return task

    if outcome.kind == "submitted":
        task.status = "submitted"
        task.attention_reason = ""
        task.next_check_at = datetime.now(timezone.utc) + timedelta(minutes=10)
        append_claim_log(task, "submitted", outcome.detail)
    else:
        detail = outcome.detail
        if outcome.screenshot_bytes and not outcome.page_text_excerpt:
            detail = await _enrich_diagnosis_with_vlm(db, ai, outcome.screenshot_bytes, detail)
        task.status = "needs_attention"
        task.attention_reason = detail
        append_claim_log(task, "needs_attention", detail)

    task.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return task


async def match_submitted_claims(
    db: AsyncSession,
    *,
    user_id: int | None = None,
) -> list[tuple[InvoiceClaimTask, Invoice]]:
    """Match ``submitted`` claim tasks against newly ingested invoices to close the loop."""
    stmt = select(InvoiceClaimTask).where(InvoiceClaimTask.status == "submitted")
    if user_id is not None:
        stmt = stmt.where(InvoiceClaimTask.user_id == user_id)
    tasks = list((await db.execute(stmt)).scalars().all())
    if not tasks:
        return []

    matched_pairs: list[tuple[InvoiceClaimTask, Invoice]] = []
    for task in tasks:
        since_ts = (task.created_at or datetime.now(timezone.utc)) - timedelta(hours=2)
        candidates = list(
            (
                await db.execute(
                    select(Invoice)
                    .where(
                        Invoice.user_id == task.user_id,
                        Invoice.created_at >= since_ts,
                    )
                    .order_by(Invoice.created_at.desc())
                    .limit(50)
                )
            ).scalars().all()
        )
        hit: Invoice | None = None
        for inv in candidates:
            amount_ok = (
                task.expected_amount is not None
                and abs(Decimal(str(inv.amount)) - Decimal(str(task.expected_amount))) <= Decimal("0.05")
            )
            merchant_ok = bool(
                task.merchant_name
                and len(task.merchant_name) >= 2
                and (
                    task.merchant_name.lower() in (inv.seller or "").lower()
                    or (inv.seller or "").lower() in task.merchant_name.lower()
                )
            )
            if amount_ok and (merchant_ok or not task.merchant_name):
                hit = inv
                break
            if merchant_ok and task.expected_amount is None:
                hit = inv
                break

        if hit is not None:
            task.matched_invoice_id = hit.id
            task.status = "completed"
            task.updated_at = datetime.now(timezone.utc)
            append_claim_log(task, "completed", f"Matched incoming invoice #{hit.id} ({hit.invoice_no})")
            matched_pairs.append((task, hit))

    if matched_pairs:
        await db.commit()
    return matched_pairs
