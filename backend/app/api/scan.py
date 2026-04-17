# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false, reportUntypedFunctionDecorator=false, reportCallInDefaultInitializer=false

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sse_starlette import EventSourceResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.database import get_db
from app.deps import CurrentUser
from app.models import ExtractionLog, ScanLog
from app.services import scan_progress as sp
from app.tasks.scheduler import scan_all_accounts

router = APIRouter(prefix="/scan", tags=["scan"])


class ScanTriggerResponse(BaseModel):
    status: str


class ScanLogResponse(BaseModel):
    id: int
    email_account_id: int
    started_at: str
    finished_at: str | None
    emails_scanned: int
    invoices_found: int
    error_message: str | None


class ScanLogListResponse(BaseModel):
    items: list[ScanLogResponse]
    total: int
    page: int
    size: int


class ExtractionLogResponse(BaseModel):
    id: int
    scan_log_id: int
    email_uid: str | None
    email_subject: str
    attachment_filename: str | None
    outcome: str
    invoice_no: str | None
    confidence: float | None
    error_detail: str | None
    created_at: str


class ExtractionLogListResponse(BaseModel):
    items: list[ExtractionLogResponse]


def _serialize_log(log: ScanLog) -> ScanLogResponse:
    return ScanLogResponse(
        id=log.id,
        email_account_id=log.email_account_id,
        started_at=log.started_at.isoformat(),
        finished_at=log.finished_at.isoformat() if log.finished_at else None,
        emails_scanned=log.emails_scanned,
        invoices_found=log.invoices_found,
        error_message=log.error_message,
    )


def _serialize_extraction(log: ExtractionLog) -> ExtractionLogResponse:
    return ExtractionLogResponse(
        id=log.id,
        scan_log_id=log.scan_log_id,
        email_uid=log.email_uid,
        email_subject=log.email_subject,
        attachment_filename=log.attachment_filename,
        outcome=log.outcome,
        invoice_no=log.invoice_no,
        confidence=log.confidence,
        error_detail=log.error_detail,
        created_at=log.created_at.isoformat(),
    )


@router.post("/trigger", response_model=ScanTriggerResponse)
async def trigger_scan(_current_user: CurrentUser) -> ScanTriggerResponse:
    if sp.is_scanning():
        raise HTTPException(status_code=409, detail="Scan already in progress")
    _ = asyncio.create_task(scan_all_accounts())
    return ScanTriggerResponse(status="triggered")


@router.get("/progress/stream")
async def progress_stream(
    request: Request,
    _current_user: CurrentUser,
) -> EventSourceResponse:
    queue = sp.subscribe()

    async def generate():
        yield {"data": sp.get_progress().to_json(), "event": "progress"}
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    yield {"data": "", "event": "ping"}
                    continue
                yield {"data": payload, "event": "progress"}
                if sp.get_progress().phase in (sp.ScanPhase.DONE, sp.ScanPhase.ERROR):
                    break
        finally:
            sp.unsubscribe(queue)

    return EventSourceResponse(
        generate(),
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/progress")
async def progress_snapshot(_current_user: CurrentUser) -> dict[str, object]:
    return sp.get_progress().to_dict()


@router.get("/logs", response_model=ScanLogListResponse)
async def list_scan_logs(
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    page: int = 1,
    size: int = 20,
) -> ScanLogListResponse:
    total = (await db.execute(select(func.count(ScanLog.id)))).scalar() or 0
    result = await db.execute(
        select(ScanLog)
        .order_by(ScanLog.started_at.desc(), ScanLog.id.desc())
        .offset(max(page - 1, 0) * size)
        .limit(size)
    )
    logs = list(result.scalars().all())
    return ScanLogListResponse(
        items=[_serialize_log(log) for log in logs],
        total=total,
        page=page,
        size=size,
    )


@router.get("/logs/{log_id}/extractions", response_model=ExtractionLogListResponse)
async def list_extraction_logs(
    log_id: int,
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> ExtractionLogListResponse:
    log = await db.get(ScanLog, log_id)
    if log is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scan log not found")

    result = await db.execute(
        select(ExtractionLog)
        .where(ExtractionLog.scan_log_id == log_id)
        .order_by(ExtractionLog.created_at.asc(), ExtractionLog.id.asc())
    )
    extractions = list(result.scalars().all())
    return ExtractionLogListResponse(items=[_serialize_extraction(item) for item in extractions])
