from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.deps import CurrentUser, assert_owned
from app.models import InvoiceClaimTask, InvoiceProfile, MaidMemory, MaidMessage
from app.services.ai_service import AIService
from app.services.claim_form_agent import execute_claim_task
from app.services.file_manager import FileManager
from app.services.maid_agent import (
    MaidAgentRunner,
    _summarize_claim_status,
    create_claim_from_receipt_photo,
    run_maid_heartbeat,
)

router = APIRouter(prefix="/maid", tags=["maid"])


class MaidChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str = "default"


class MaidChatResponse(BaseModel):
    reply: str
    tool_events: list[dict[str, Any]]
    session_id: str


class MaidMessageResponse(BaseModel):
    id: int
    role: str
    content: str
    tool_events: list[dict[str, Any]]
    created_at: str


class MaidMemoryResponse(BaseModel):
    id: int
    category: str
    rule_text: str
    match_pattern: str
    action_json: str
    is_active: bool
    created_at: str


class MaidMemoryCreate(BaseModel):
    category: str = "reimbursement"
    rule_text: str = Field(min_length=1, max_length=500)
    match_pattern: str = ""
    action_json: str = "{}"


class ClaimTaskResponse(BaseModel):
    id: int
    profile_id: int | None
    source_channel: str
    qr_url: str
    merchant_name: str
    expected_amount: float | None
    expected_currency: str
    receipt_date: str | None
    receipt_summary: str
    status: str
    attention_reason: str
    has_screenshot: bool
    matched_invoice_id: int | None
    attempts: int
    logs: list[dict[str, Any]]
    created_at: str
    updated_at: str


class ClaimFromUrlRequest(BaseModel):
    qr_url: str = Field(min_length=5)


class ClaimIntakeResponse(BaseModel):
    task: ClaimTaskResponse
    needs_profile_choice: bool
    profiles: list[dict[str, Any]]
    message: str


class ClaimSelectProfileRequest(BaseModel):
    profile_id: int


class ClaimRetryRequest(BaseModel):
    captcha_input: str = ""


def _serialize_claim(task: InvoiceClaimTask) -> ClaimTaskResponse:
    try:
        logs = json.loads(task.log_json or "[]")
        if not isinstance(logs, list):
            logs = []
    except (TypeError, ValueError):
        logs = []
    return ClaimTaskResponse(
        id=task.id,
        profile_id=task.profile_id,
        source_channel=task.source_channel,
        qr_url=task.qr_url,
        merchant_name=task.merchant_name,
        expected_amount=float(task.expected_amount) if task.expected_amount is not None else None,
        expected_currency=task.expected_currency,
        receipt_date=task.receipt_date.isoformat() if task.receipt_date else None,
        receipt_summary=task.receipt_summary,
        status=task.status,
        attention_reason=task.attention_reason,
        has_screenshot=bool(task.last_screenshot_path),
        matched_invoice_id=task.matched_invoice_id,
        attempts=task.attempts,
        logs=logs,
        created_at=task.created_at.isoformat(),
        updated_at=task.updated_at.isoformat(),
    )


def _serialize_memory(mem: MaidMemory) -> MaidMemoryResponse:
    return MaidMemoryResponse(
        id=mem.id,
        category=mem.category,
        rule_text=mem.rule_text,
        match_pattern=mem.match_pattern,
        action_json=mem.action_json,
        is_active=mem.is_active,
        created_at=mem.created_at.isoformat(),
    )


@router.post("/chat", response_model=MaidChatResponse)
async def maid_chat(
    payload: MaidChatRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> MaidChatResponse:
    runner = MaidAgentRunner(get_settings())
    result = await runner.chat(
        db,
        user_id=current_user.id,
        user_message=payload.message,
        session_id=payload.session_id,
    )
    return MaidChatResponse(
        reply=result.reply,
        tool_events=result.tool_events,
        session_id=result.session_id,
    )


@router.get("/history", response_model=list[MaidMessageResponse])
async def maid_history(
    current_user: CurrentUser,
    session_id: str = "default",
    db: AsyncSession = Depends(get_db),
) -> list[MaidMessageResponse]:
    rows = list(
        (
            await db.execute(
                select(MaidMessage)
                .where(
                    MaidMessage.user_id == current_user.id,
                    MaidMessage.session_id == session_id,
                )
                .order_by(MaidMessage.id.asc())
                .limit(100)
            )
        ).scalars().all()
    )
    out: list[MaidMessageResponse] = []
    for row in rows:
        try:
            events = json.loads(row.tool_calls_json or "[]")
            if not isinstance(events, list):
                events = []
        except (TypeError, ValueError):
            events = []
        out.append(
            MaidMessageResponse(
                id=row.id,
                role=row.role,
                content=row.content,
                tool_events=events,
                created_at=row.created_at.isoformat(),
            )
        )
    return out


@router.delete("/history", status_code=status.HTTP_204_NO_CONTENT)
async def clear_maid_history(
    current_user: CurrentUser,
    session_id: str = "default",
    db: AsyncSession = Depends(get_db),
) -> Response:
    await db.execute(
        delete(MaidMessage).where(
            MaidMessage.user_id == current_user.id,
            MaidMessage.session_id == session_id,
        )
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/memories", response_model=list[MaidMemoryResponse])
async def list_memories(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> list[MaidMemoryResponse]:
    rows = list(
        (
            await db.execute(
                select(MaidMemory)
                .where(MaidMemory.user_id == current_user.id)
                .order_by(MaidMemory.id.desc())
            )
        ).scalars().all()
    )
    return [_serialize_memory(m) for m in rows]


@router.post("/memories", response_model=MaidMemoryResponse, status_code=status.HTTP_201_CREATED)
async def create_memory(
    payload: MaidMemoryCreate,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> MaidMemoryResponse:
    mem = MaidMemory(
        user_id=current_user.id,
        category=payload.category.strip() or "general",
        rule_text=payload.rule_text.strip(),
        match_pattern=payload.match_pattern.strip(),
        action_json=payload.action_json or "{}",
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    db.add(mem)
    await db.commit()
    await db.refresh(mem)
    return _serialize_memory(mem)


@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: int,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> Response:
    mem = assert_owned(await db.get(MaidMemory, memory_id), current_user)
    await db.delete(mem)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/claims", response_model=list[ClaimTaskResponse])
async def list_claims(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> list[ClaimTaskResponse]:
    rows = list(
        (
            await db.execute(
                select(InvoiceClaimTask)
                .where(InvoiceClaimTask.user_id == current_user.id)
                .order_by(InvoiceClaimTask.id.desc())
                .limit(50)
            )
        ).scalars().all()
    )
    return [_serialize_claim(t) for t in rows]


@router.post("/claims/from-photo", response_model=ClaimIntakeResponse, status_code=status.HTTP_201_CREATED)
async def create_claim_from_photo(
    current_user: CurrentUser,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> ClaimIntakeResponse:
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty image upload")
    settings = get_settings()
    intake = await create_claim_from_receipt_photo(
        db,
        user_id=current_user.id,
        image_bytes=image_bytes,
        settings=settings,
        ai=AIService(settings),
        source_channel="web",
    )
    return ClaimIntakeResponse(
        task=_serialize_claim(intake.task),
        needs_profile_choice=intake.needs_profile_choice,
        profiles=[
            {"id": p.id, "name": p.name, "title": p.title, "tax_id": p.tax_id}
            for p in intake.profiles
        ],
        message=intake.message,
    )


@router.post("/claims/from-url", response_model=ClaimIntakeResponse, status_code=status.HTTP_201_CREATED)
async def create_claim_from_url(
    payload: ClaimFromUrlRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> ClaimIntakeResponse:
    settings = get_settings()
    intake = await create_claim_from_receipt_photo(
        db,
        user_id=current_user.id,
        image_bytes=b"",
        settings=settings,
        ai=AIService(settings),
        source_channel="web",
        explicit_qr_url=payload.qr_url,
    )
    return ClaimIntakeResponse(
        task=_serialize_claim(intake.task),
        needs_profile_choice=intake.needs_profile_choice,
        profiles=[
            {"id": p.id, "name": p.name, "title": p.title, "tax_id": p.tax_id}
            for p in intake.profiles
        ],
        message=intake.message,
    )


@router.post("/claims/{task_id}/select-profile", response_model=ClaimIntakeResponse)
async def select_claim_profile(
    task_id: int,
    payload: ClaimSelectProfileRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> ClaimIntakeResponse:
    task = assert_owned(await db.get(InvoiceClaimTask, task_id), current_user)
    profile = assert_owned(await db.get(InvoiceProfile, payload.profile_id), current_user)
    settings = get_settings()
    task = await execute_claim_task(
        db,
        task=task,
        profile=profile,
        settings=settings,
        ai=AIService(settings),
        file_mgr=FileManager(settings.STORAGE_PATH),
    )
    return ClaimIntakeResponse(
        task=_serialize_claim(task),
        needs_profile_choice=False,
        profiles=[],
        message=_summarize_claim_status(task, profile),
    )


@router.post("/claims/{task_id}/retry", response_model=ClaimIntakeResponse)
async def retry_claim_task(
    task_id: int,
    payload: ClaimRetryRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> ClaimIntakeResponse:
    task = assert_owned(await db.get(InvoiceClaimTask, task_id), current_user)
    if task.profile_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Select an invoice profile first",
        )
    profile = assert_owned(await db.get(InvoiceProfile, task.profile_id), current_user)
    settings = get_settings()
    task = await execute_claim_task(
        db,
        task=task,
        profile=profile,
        settings=settings,
        ai=AIService(settings),
        file_mgr=FileManager(settings.STORAGE_PATH),
        captcha_input=payload.captcha_input,
    )
    return ClaimIntakeResponse(
        task=_serialize_claim(task),
        needs_profile_choice=False,
        profiles=[],
        message=_summarize_claim_status(task, profile),
    )


@router.post("/claims/{task_id}/cancel", response_model=ClaimTaskResponse)
async def cancel_claim_task(
    task_id: int,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> ClaimTaskResponse:
    task = assert_owned(await db.get(InvoiceClaimTask, task_id), current_user)
    task.status = "cancelled"
    task.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(task)
    return _serialize_claim(task)


@router.get("/claims/{task_id}/screenshot")
async def get_claim_screenshot(
    task_id: int,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    task = assert_owned(await db.get(InvoiceClaimTask, task_id), current_user)
    if not task.last_screenshot_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Screenshot not found")
    full = Path(get_settings().STORAGE_PATH).expanduser() / task.last_screenshot_path
    if not full.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Screenshot file missing")
    return FileResponse(path=full, media_type="image/png", filename=f"claim_{task.id}.png")


@router.post("/heartbeat")
async def trigger_maid_heartbeat(
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    return await run_maid_heartbeat(db, settings=get_settings())
