"""Playwright + deterministic/LLM form-filling agent for QR invoice claim tasks.

Policy (per user instruction):
  - Auto-fill and auto-submit without asking confirmation on normal forms.
  - Escalate to ``needs_attention`` (saving a screenshot + clear reason) ONLY when
    something unexpected occurs: WeChat-only link, CAPTCHA/SMS code required,
    unrecognized/missing required fields, or post-submit error message.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Invoice, InvoiceClaimTask, InvoiceProfile
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
    "invoice submitted",
    "successfully submitted",
)

ERROR_TEXT_SIGNALS = (
    "验证码错误",
    "格式不正确",
    "不能为空",
    "开票失败",
    "已开具",
    "超出开票期限",
    "二维码已失效",
    "请在微信客户端打开",
    "请在微信中打开",
    "系统繁忙",
)

CAPTCHA_FIELD_HINTS = ("验证码", "captcha", "verify", "vcode", "图形码", "校验码")


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

    kind: str  # "submitted" | "pdf_downloaded" | "needs_attention" | "failed"
    detail: str
    screenshot_bytes: bytes | None = None
    pdf_bytes: bytes | None = None
    pdf_filename: str = "claimed_invoice.pdf"


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
    """Plain-text header card for quick copy-paste when a QR code is WeChat-only."""
    lines = [
        f"抬头名称：{profile.title}",
    ]
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
    return "needs_attention", "已点击提交但未能确认开票成功提示，请核对截图。"


async def _default_playwright_runner(  # pragma: no cover - real headless browser path
    qr_url: str,
    profile: InvoiceProfile,
    captcha_input: str = "",
) -> BrowserClaimOutcome:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return BrowserClaimOutcome(
            kind="needs_attention",
            detail="服务器未安装 Playwright 浏览器，请手工开票。",
        )

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
            resp = await page.goto(qr_url, wait_until="networkidle", timeout=30_000)
            if resp is not None:
                ctype = (resp.headers.get("content-type") or "").lower()
                if "application/pdf" in ctype:
                    pdf_bytes = await resp.body()
                    return BrowserClaimOutcome(
                        kind="pdf_downloaded",
                        detail="开票链接直接返回 PDF 发票",
                        pdf_bytes=pdf_bytes,
                    )

            body_text = await page.inner_text("body")
            if "请在微信" in body_text:
                shot = await page.screenshot(full_page=True)
                return BrowserClaimOutcome(
                    kind="needs_attention",
                    detail="该开票页面限制仅在微信客户端内打开。\n" + format_copyable_profile_card(profile),
                    screenshot_bytes=shot,
                )

            raw_fields = await page.evaluate(
                """() => {
                    const out = [];
                    const els = document.querySelectorAll('input, textarea');
                    let idx = 0;
                    for (const el of els) {
                        if (el.offsetParent === null) continue;
                        idx += 1;
                        el.setAttribute('data-maid-idx', String(idx));
                        const labelEl = el.id ? document.querySelector(`label[for="${el.id}"]`) : el.closest('label');
                        out.push({
                            selector: `[data-maid-idx="${idx}"]`,
                            label: labelEl ? labelEl.innerText.trim() : '',
                            placeholder: el.getAttribute('placeholder') || '',
                            name: el.getAttribute('name') || el.id || '',
                            field_type: (el.getAttribute('type') || 'text').toLowerCase(),
                        });
                    }
                    return out;
                }"""
            )
            fields = [FormFieldInfo(**f) for f in raw_fields]
            fills, attention = plan_deterministic_fills(fields, profile, captcha_input=captcha_input)
            if attention:
                shot = await page.screenshot(full_page=True)
                return BrowserClaimOutcome(kind="needs_attention", detail=attention, screenshot_bytes=shot)
            if not fills:
                shot = await page.screenshot(full_page=True)
                return BrowserClaimOutcome(
                    kind="needs_attention",
                    detail="未在页面中识别到可自动填写的开票表单字段，请查看截图或手工开票。",
                    screenshot_bytes=shot,
                )

            for selector, value in fills:
                await page.locator(selector).first.fill(value)

            submit_btn = page.locator(
                'button:has-text("提交"), button:has-text("确认开票"), button:has-text("申请开票"), '
                'button:has-text("立即开票"), button[type="submit"], a:has-text("提交")'
            ).first
            if await submit_btn.count() == 0:
                shot = await page.screenshot(full_page=True)
                return BrowserClaimOutcome(
                    kind="needs_attention",
                    detail="表单已填写但未找到提交按钮，请查看截图确认。",
                    screenshot_bytes=shot,
                )

            await submit_btn.click()
            await page.wait_for_timeout(3000)
            post_text = await page.inner_text("body")
            status_kind, detail = evaluate_post_submit_text(post_text)
            shot = await page.screenshot(full_page=True) if status_kind != "submitted" else None
            return BrowserClaimOutcome(kind=status_kind, detail=detail, screenshot_bytes=shot)
        except Exception as exc:
            try:
                shot = await page.screenshot(full_page=True)
            except Exception:
                shot = None
            return BrowserClaimOutcome(
                kind="needs_attention",
                detail=f"自动填表遇到异常：{exc}"[:500],
                screenshot_bytes=shot,
            )
        finally:
            await browser.close()


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
) -> InvoiceClaimTask:
    """Execute one attempt of a QR invoice claim task with auto-submit & error escalation."""
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

    runner = browser_runner or _default_playwright_runner
    outcome = await runner(task.qr_url, profile, captcha_input)

    if outcome.screenshot_bytes:
        task.last_screenshot_path = _save_claim_screenshot(
            settings.STORAGE_PATH, task.user_id, task.id, outcome.screenshot_bytes
        )

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
        task.status = "needs_attention"
        task.attention_reason = outcome.detail
        append_claim_log(task, "needs_attention", outcome.detail)

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
