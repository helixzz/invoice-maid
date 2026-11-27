"""IM-agnostic Invoice Maid v2.0 Agent Loop runner, tool registry, memory rules, and heartbeat."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import Invoice, InvoiceClaimTask, InvoiceProfile, MaidMemory, MaidMessage
from app.services.ai_service import AIService
from app.services.claim_form_agent import (
    BrowserRunner,
    append_claim_log,
    execute_claim_task,
    match_submitted_claims,
)
from app.services.file_manager import FileManager
from app.services.qr_receipt_decoder import analyze_receipt_photo, is_wechat_only_url
from app.services.search_service import SearchService

logger = logging.getLogger(__name__)

MAX_AGENT_STEPS = 6

MAID_SYSTEM_PROMPT = """你是米尔（Miri），Invoice Maid 的发票小帮手。
性格细心可靠、记性很好，待人礼貌亲切，回复简洁明了、说人话，不堆砌套话。
你可以调用工具帮用户：
1. 查询/筛选/统计发票（支持真实多币种与报销状态）；
2. 批量修改发票报销状态或标签；
3. 查看与管理预存发票抬头、跟进线下扫码开票任务（选抬头、输入验证码重试、查看状态）；
4. 记住用户的长期报销/分类偏好规则（写入记忆）。

工作准则：
- 涉及查账、统计、改状态、选抬头、保存规则时，优先调用对应工具拿到真实数据再回答，绝不编造发票号或金额。
- 回答简短清楚，直接给结论、关键数字和下一步建议。
- 绝不删除发票或修改邮箱密码凭证。
"""

MAID_TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_invoices",
            "description": "Search user invoices by keyword, category, currency, reimbursement_status, or date range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keyword to match seller, buyer, item_summary, or invoice_no"},
                    "category": {"type": "string", "description": "Optional invoice_category filter"},
                    "currency": {"type": "string", "description": "Optional ISO currency code e.g. CNY, USD, EUR"},
                    "reimbursement_status": {
                        "type": "string",
                        "enum": ["unreimbursed", "pending", "reimbursed", "not_applicable"],
                    },
                    "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                    "date_to": {"type": "string", "description": "YYYY-MM-DD"},
                    "limit": {"type": "integer", "default": 10},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_spend_summary",
            "description": "Get invoice totals grouped by currency and reimbursement status.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_reimbursement",
            "description": "Batch update reimbursement_status and/or tags for specified invoice IDs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "invoice_ids": {"type": "array", "items": {"type": "integer"}},
                    "reimbursement_status": {
                        "type": "string",
                        "enum": ["unreimbursed", "pending", "reimbursed", "not_applicable"],
                    },
                    "tags": {"type": "string"},
                },
                "required": ["invoice_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_invoice_profiles",
            "description": "List saved invoice header profiles (title, tax_id, email).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_claim_tasks",
            "description": "List QR-code offline invoice claim tasks and their status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "Optional status filter"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "select_claim_profile",
            "description": "Select an invoice profile for an awaiting QR claim task and auto-fill/submit it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer"},
                    "profile_id": {"type": "integer"},
                },
                "required": ["task_id", "profile_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retry_claim_task",
            "description": "Retry a QR claim task (optionally supplying a captcha code from the user).",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer"},
                    "captcha_input": {"type": "string"},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_memories",
            "description": "List active long-term maid memory rules for the user.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_memory_rule",
            "description": "Save a persistent preference or automation rule (e.g., auto-mark pharmacy invoices as not_applicable).",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "default": "reimbursement"},
                    "rule_text": {"type": "string"},
                    "match_pattern": {"type": "string", "description": "Optional substring matched against seller/summary"},
                    "reimbursement_status": {
                        "type": "string",
                        "enum": ["unreimbursed", "pending", "reimbursed", "not_applicable"],
                    },
                    "tags": {"type": "string"},
                },
                "required": ["rule_text"],
            },
        },
    },
]


@dataclass
class MaidChatResult:
    reply: str
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    session_id: str = "default"


@dataclass
class PhotoClaimIntakeResult:
    task: InvoiceClaimTask
    profiles: list[InvoiceProfile] = field(default_factory=list)
    needs_profile_choice: bool = False
    message: str = ""


async def apply_maid_memories_to_invoice(db: AsyncSession, invoice: Invoice) -> bool:
    """Apply matching active MaidMemory automation rules to a newly ingested invoice."""
    memories = list(
        (
            await db.execute(
                select(MaidMemory).where(
                    MaidMemory.user_id == invoice.user_id,
                    MaidMemory.is_active.is_(True),
                )
            )
        ).scalars().all()
    )
    if not memories:
        return False

    haystack = f"{invoice.seller} {invoice.buyer} {invoice.item_summary or ''}".lower()
    changed = False
    for mem in memories:
        pattern = (mem.match_pattern or "").strip().lower()
        if not pattern or pattern not in haystack:
            continue
        try:
            action = json.loads(mem.action_json or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(action, dict):
            continue
        new_status = action.get("reimbursement_status")
        if new_status in ("unreimbursed", "pending", "reimbursed", "not_applicable"):
            invoice.reimbursement_status = new_status
            changed = True
        extra_tags = str(action.get("tags") or "").strip()
        if extra_tags:
            existing_tags = [t.strip() for t in (invoice.tags or "").split(",") if t.strip()]
            for tag in [t.strip() for t in extra_tags.split(",") if t.strip()]:
                if tag not in existing_tags:
                    existing_tags.append(tag)
            invoice.tags = ",".join(existing_tags)
            changed = True
    return changed


async def create_claim_from_receipt_photo(
    db: AsyncSession,
    *,
    user_id: int,
    image_bytes: bytes,
    settings: Settings,
    ai: Any,
    source_channel: str = "web",
    source_chat_id: str = "",
    explicit_qr_url: str = "",
    browser_runner: BrowserRunner | None = None,
) -> PhotoClaimIntakeResult:
    """Intake an offline receipt photo (or direct QR URL), decode QR/metadata, and either
    prompt for profile selection (when multiple profiles exist) or auto-submit immediately."""
    if explicit_qr_url:
        qr_url = explicit_qr_url.strip()
        merchant_name = ""
        amount = None
        currency = "CNY"
        receipt_date = None
        summary = ""
    else:
        decoded = await analyze_receipt_photo(db, ai, image_bytes)
        qr_url = decoded.primary_qr_url
        merchant_name = decoded.merchant_name
        amount = decoded.amount
        currency = decoded.currency
        receipt_date = decoded.receipt_date
        summary = decoded.summary

    now = datetime.now(timezone.utc)
    task = InvoiceClaimTask(
        user_id=user_id,
        source_channel=source_channel,
        source_chat_id=source_chat_id,
        qr_url=qr_url,
        merchant_name=merchant_name,
        expected_amount=amount,
        expected_currency=currency,
        receipt_date=receipt_date,
        receipt_summary=summary,
        status="awaiting_profile",
        created_at=now,
        updated_at=now,
    )
    append_claim_log(task, "created", f"channel={source_channel} url={qr_url[:120]}")
    db.add(task)
    await db.commit()
    await db.refresh(task)

    if not qr_url:
        task.status = "needs_attention"
        task.attention_reason = "未能从照片中识别出开票二维码，请靠近拍清二维码或直接发送开票链接。"
        append_claim_log(task, "no_qr", task.attention_reason)
        await db.commit()
        return PhotoClaimIntakeResult(task=task, message=task.attention_reason)

    profiles = list(
        (
            await db.execute(
                select(InvoiceProfile)
                .where(InvoiceProfile.user_id == user_id)
                .order_by(InvoiceProfile.is_default.desc(), InvoiceProfile.id.asc())
            )
        ).scalars().all()
    )

    if not profiles:
        task.status = "awaiting_profile"
        task.attention_reason = "尚未配置发票抬头，请先在「设置 → 发票抬头」中添加抬头后再继续。"
        await db.commit()
        return PhotoClaimIntakeResult(task=task, profiles=[], message=task.attention_reason)

    if len(profiles) > 1:
        merchant_hint = f"（{merchant_name} {currency} {amount}）" if merchant_name and amount else ""
        msg = f"已识别到开票二维码{merchant_hint}！检测到您有 {len(profiles)} 个预存发票抬头，请选择本次开票要使用的抬头："
        return PhotoClaimIntakeResult(
            task=task,
            profiles=profiles,
            needs_profile_choice=True,
            message=msg,
        )

    # Single profile -> auto-fill and auto-submit immediately (no confirmation needed!)
    file_mgr = FileManager(settings.STORAGE_PATH)
    task = await execute_claim_task(
        db,
        task=task,
        profile=profiles[0],
        settings=settings,
        ai=ai,
        file_mgr=file_mgr,
        browser_runner=browser_runner,
    )
    return PhotoClaimIntakeResult(
        task=task,
        profiles=profiles,
        needs_profile_choice=False,
        message=_summarize_claim_status(task, profiles[0]),
    )


def _summarize_claim_status(task: InvoiceClaimTask, profile: InvoiceProfile | None = None) -> str:
    prof_label = f"「{profile.name}」" if profile is not None else ""
    if task.status == "completed":
        return f"🎉 开票并归档完成！使用抬头{prof_label}，已关联发票 #{task.matched_invoice_id}。"
    if task.status == "submitted":
        return f"✅ 已使用抬头{prof_label}自动提交开票申请！我会持续跟进邮箱收票，到账后立刻通知您。"
    if is_wechat_only_url(task.qr_url):
        return f"⚠️ {task.attention_reason}"
    return f"⚠️ 自动开票遇到需要您确认的情况：{task.attention_reason}"


class MaidAgentRunner:
    """IM-agnostic Tool-Calling Agent Loop for Invoice Maid v2.0."""

    def __init__(
        self,
        settings: Settings | None = None,
        ai: AIService | None = None,
        browser_runner: BrowserRunner | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._ai = ai or AIService(self._settings)
        self._browser_runner = browser_runner

    async def chat(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        user_message: str,
        session_id: str = "default",
    ) -> MaidChatResult:
        now = datetime.now(timezone.utc)
        db.add(
            MaidMessage(
                user_id=user_id,
                session_id=session_id,
                role="user",
                content=user_message,
                created_at=now,
            )
        )
        await db.commit()

        history_rows = list(
            (
                await db.execute(
                    select(MaidMessage)
                    .where(
                        MaidMessage.user_id == user_id,
                        MaidMessage.session_id == session_id,
                    )
                    .order_by(MaidMessage.id.desc())
                    .limit(12)
                )
            ).scalars().all()
        )
        history_rows.reverse()

        memories = list(
            (
                await db.execute(
                    select(MaidMemory)
                    .where(MaidMemory.user_id == user_id, MaidMemory.is_active.is_(True))
                    .limit(20)
                )
            ).scalars().all()
        )
        system_content = MAID_SYSTEM_PROMPT
        if memories:
            mem_lines = "\n".join(f"- [{m.category}] {m.rule_text}" for m in memories)
            system_content = f"{system_content}\n\n已记住的主人偏好规则：\n{mem_lines}"

        messages: list[dict[str, Any]] = [{"role": "system", "content": system_content}]
        for row in history_rows:
            if row.role in ("user", "assistant"):
                messages.append({"role": row.role, "content": row.content})

        tool_events: list[dict[str, Any]] = []
        final_reply = ""

        for _step in range(MAX_AGENT_STEPS):
            msg = await self._ai.chat_with_tools(db, messages, tools=MAID_TOOLS_SCHEMA)
            tool_calls = getattr(msg, "tool_calls", None) or []
            if not tool_calls:
                final_reply = (getattr(msg, "content", None) or "").strip() or "已处理完毕。"
                break

            assistant_tool_calls = []
            for tc in tool_calls:
                assistant_tool_calls.append(
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                )
            messages.append(
                {
                    "role": "assistant",
                    "content": getattr(msg, "content", None) or "",
                    "tool_calls": assistant_tool_calls,
                }
            )

            for tc in tool_calls:
                fn_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    if not isinstance(args, dict):
                        args = {}
                except (TypeError, ValueError):
                    args = {}
                tool_output = await self._execute_tool(db, user_id=user_id, name=fn_name, args=args)
                tool_events.append({"tool": fn_name, "args": args, "result": tool_output})
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(tool_output, ensure_ascii=False, default=str),
                    }
                )
        else:
            final_reply = "已完成上述查询与操作，请查看工具执行结果。"

        db.add(
            MaidMessage(
                user_id=user_id,
                session_id=session_id,
                role="assistant",
                content=final_reply,
                tool_calls_json=json.dumps(tool_events, ensure_ascii=False, default=str),
                created_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()
        return MaidChatResult(reply=final_reply, tool_events=tool_events, session_id=session_id)

    async def _execute_tool(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        if name == "search_invoices":
            svc = SearchService(self._settings)
            d_from = date.fromisoformat(args["date_from"]) if args.get("date_from") else None
            d_to = date.fromisoformat(args["date_to"]) if args.get("date_to") else None
            items, total = await svc.search_fts(
                db=db,
                query=str(args.get("query") or ""),
                user_id=user_id,
                date_from=d_from,
                date_to=d_to,
                categories=[args["category"]] if args.get("category") else None,
                currencies=[args["currency"]] if args.get("currency") else None,
                reimbursement_statuses=[args["reimbursement_status"]] if args.get("reimbursement_status") else None,
                page=1,
                size=min(max(int(args.get("limit") or 10), 1), 30),
            )
            return {
                "total": total,
                "items": [
                    {
                        "id": i.id,
                        "invoice_no": i.invoice_no,
                        "date": i.invoice_date.isoformat(),
                        "seller": i.seller,
                        "buyer": i.buyer,
                        "amount": float(i.amount),
                        "currency": i.currency,
                        "category": i.invoice_category,
                        "reimbursement_status": i.reimbursement_status,
                        "tags": i.tags,
                    }
                    for i in items
                ],
            }

        if name == "get_spend_summary":
            cur_rows = (
                await db.execute(
                    select(
                        Invoice.currency,
                        func.count(Invoice.id),
                        func.coalesce(func.sum(Invoice.amount), 0),
                    )
                    .where(Invoice.user_id == user_id)
                    .group_by(Invoice.currency)
                )
            ).all()
            st_rows = (
                await db.execute(
                    select(Invoice.reimbursement_status, func.count(Invoice.id))
                    .where(Invoice.user_id == user_id)
                    .group_by(Invoice.reimbursement_status)
                )
            ).all()
            return {
                "by_currency": [
                    {"currency": r[0], "count": r[1], "total": float(r[2])} for r in cur_rows
                ],
                "by_reimbursement_status": {r[0]: r[1] for r in st_rows},
            }

        if name == "update_reimbursement":
            ids = [int(x) for x in (args.get("invoice_ids") or [])]
            if not ids:
                return {"updated": 0}
            rows = list(
                (
                    await db.execute(
                        select(Invoice).where(Invoice.user_id == user_id, Invoice.id.in_(ids))
                    )
                ).scalars().all()
            )
            for inv in rows:
                if args.get("reimbursement_status"):
                    inv.reimbursement_status = str(args["reimbursement_status"])
                if args.get("tags") is not None:
                    inv.tags = str(args["tags"])
            await db.commit()
            return {"updated": len(rows)}

        if name == "list_invoice_profiles":
            profiles = list(
                (
                    await db.execute(
                        select(InvoiceProfile)
                        .where(InvoiceProfile.user_id == user_id)
                        .order_by(InvoiceProfile.is_default.desc(), InvoiceProfile.id.asc())
                    )
                ).scalars().all()
            )
            return {
                "profiles": [
                    {
                        "id": p.id,
                        "name": p.name,
                        "title": p.title,
                        "tax_id": p.tax_id,
                        "email": p.email,
                        "is_default": p.is_default,
                    }
                    for p in profiles
                ]
            }

        if name == "list_claim_tasks":
            stmt = select(InvoiceClaimTask).where(InvoiceClaimTask.user_id == user_id)
            if args.get("status"):
                stmt = stmt.where(InvoiceClaimTask.status == str(args["status"]))
            tasks = list((await db.execute(stmt.order_by(InvoiceClaimTask.id.desc()).limit(20))).scalars().all())
            return {
                "tasks": [
                    {
                        "id": t.id,
                        "status": t.status,
                        "merchant_name": t.merchant_name,
                        "expected_amount": float(t.expected_amount) if t.expected_amount is not None else None,
                        "attention_reason": t.attention_reason,
                        "matched_invoice_id": t.matched_invoice_id,
                    }
                    for t in tasks
                ]
            }

        if name == "select_claim_profile":
            task = await db.get(InvoiceClaimTask, int(args["task_id"]))
            profile = await db.get(InvoiceProfile, int(args["profile_id"]))
            if task is None or task.user_id != user_id:
                return {"error": "claim task not found"}
            if profile is None or profile.user_id != user_id:
                return {"error": "invoice profile not found"}
            task = await execute_claim_task(
                db,
                task=task,
                profile=profile,
                settings=self._settings,
                ai=self._ai,
                file_mgr=FileManager(self._settings.STORAGE_PATH),
                browser_runner=self._browser_runner,
            )
            return {
                "task_id": task.id,
                "status": task.status,
                "message": _summarize_claim_status(task, profile),
            }

        if name == "retry_claim_task":
            task = await db.get(InvoiceClaimTask, int(args["task_id"]))
            if task is None or task.user_id != user_id or task.profile_id is None:
                return {"error": "claim task or selected profile not found"}
            profile = await db.get(InvoiceProfile, task.profile_id)
            if profile is None:
                return {"error": "profile not found"}
            task = await execute_claim_task(
                db,
                task=task,
                profile=profile,
                settings=self._settings,
                ai=self._ai,
                file_mgr=FileManager(self._settings.STORAGE_PATH),
                captcha_input=str(args.get("captcha_input") or ""),
                browser_runner=self._browser_runner,
            )
            return {
                "task_id": task.id,
                "status": task.status,
                "message": _summarize_claim_status(task, profile),
            }

        if name == "list_memories":
            rows = list(
                (
                    await db.execute(
                        select(MaidMemory)
                        .where(MaidMemory.user_id == user_id, MaidMemory.is_active.is_(True))
                        .order_by(MaidMemory.id.desc())
                    )
                ).scalars().all()
            )
            return {
                "memories": [
                    {
                        "id": m.id,
                        "category": m.category,
                        "rule_text": m.rule_text,
                        "match_pattern": m.match_pattern,
                    }
                    for m in rows
                ]
            }

        if name == "save_memory_rule":
            action: dict[str, Any] = {}
            if args.get("reimbursement_status"):
                action["reimbursement_status"] = str(args["reimbursement_status"])
            if args.get("tags"):
                action["tags"] = str(args["tags"])
            mem = MaidMemory(
                user_id=user_id,
                category=str(args.get("category") or "reimbursement"),
                rule_text=str(args["rule_text"])[:500],
                match_pattern=str(args.get("match_pattern") or "")[:255],
                action_json=json.dumps(action, ensure_ascii=False),
                is_active=True,
                created_at=datetime.now(timezone.utc),
            )
            db.add(mem)
            await db.commit()
            await db.refresh(mem)
            return {"saved_memory_id": mem.id, "rule_text": mem.rule_text}

        return {"error": f"Unknown tool: {name}"}


async def run_maid_heartbeat(
    db: AsyncSession,
    *,
    settings: Settings | None = None,
    ai: AIService | None = None,
    browser_runner: BrowserRunner | None = None,
) -> dict[str, int]:
    """Periodic Agent Loop heartbeat: process queued claims, match submitted claims to new invoices."""
    resolved_settings = settings or get_settings()
    resolved_ai = ai or AIService(resolved_settings)
    file_mgr = FileManager(resolved_settings.STORAGE_PATH)

    queued_tasks = list(
        (
            await db.execute(
                select(InvoiceClaimTask).where(
                    InvoiceClaimTask.status == "queued",
                    InvoiceClaimTask.profile_id.is_not(None),
                )
            )
        ).scalars().all()
    )
    processed_queued = 0
    for task in queued_tasks:
        profile = await db.get(InvoiceProfile, task.profile_id)
        if profile is None:
            continue
        await execute_claim_task(
            db,
            task=task,
            profile=profile,
            settings=resolved_settings,
            ai=resolved_ai,
            file_mgr=file_mgr,
            browser_runner=browser_runner,
        )
        processed_queued += 1

    matched = await match_submitted_claims(db)
    if matched:
        from app.services.telegram_notifier import resolve_telegram_settings, send_telegram_message

        cfg = await resolve_telegram_settings(db)
        if cfg["enabled"] and cfg["bot_token"] and cfg["chat_id"]:
            for task, inv in matched:
                await send_telegram_message(
                    cfg["bot_token"],
                    cfg["chat_id"],
                    f"🎉 <b>线下扫码开票已自动归档！</b>\n"
                    f"商户：{inv.seller}\n"
                    f"金额：{inv.currency} {inv.amount}\n"
                    f"发票号：<code>{inv.invoice_no}</code>",
                )

    return {"processed_queued": processed_queued, "matched_claims": len(matched)}
