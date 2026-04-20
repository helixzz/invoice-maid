# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false, reportUntypedFunctionDecorator=false, reportCallInDefaultInitializer=false

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel
from sse_starlette import EventSourceResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.database import get_db
from app.deps import CurrentUser, assert_owned
from app.models import EmailAccount, ExtractionLog, ScanLog
from app.services import scan_progress as sp
from app.services.email_scanner import ScanOptions
from app.tasks.scheduler import scan_all_accounts

router = APIRouter(prefix="/scan", tags=["scan"])


class ScanTriggerResponse(BaseModel):
    status: str


class ScanTriggerRequest(BaseModel):
    full: bool = False
    unread_only: bool = False
    since: datetime | None = None


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
    classification_tier: int | None
    parse_method: str | None
    parse_format: str | None
    download_outcome: str | None
    invoice_no: str | None
    confidence: float | None
    error_detail: str | None
    created_at: str


class ExtractionLogListResponse(BaseModel):
    items: list[ExtractionLogResponse]


class ExtractionSummaryResponse(BaseModel):
    scan_log_id: int
    total: int
    outcomes: dict[str, int]
    parse_methods: dict[str, int]
    classification_tiers: dict[str, int]


def _serialize_log(log: ScanLog) -> ScanLogResponse:
    started = log.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    finished = log.finished_at
    if finished is not None and finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    return ScanLogResponse(
        id=log.id,
        email_account_id=log.email_account_id,
        started_at=started.isoformat(),
        finished_at=finished.isoformat() if finished else None,
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
        classification_tier=log.classification_tier,
        parse_method=log.parse_method,
        parse_format=log.parse_format,
        download_outcome=log.download_outcome,
        invoice_no=log.invoice_no,
        confidence=log.confidence,
        error_detail=log.error_detail,
        created_at=log.created_at.isoformat(),
    )


@router.post("/trigger", response_model=ScanTriggerResponse)
async def trigger_scan(
    _current_user: CurrentUser,
    full: bool = False,
    body: ScanTriggerRequest | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
) -> ScanTriggerResponse:
    if sp.is_scanning():
        raise HTTPException(status_code=409, detail="Scan already in progress")
    effective_full = body.full if body is not None else full
    unread_only = body.unread_only if body is not None else False
    since = body.since if body is not None else None
    if effective_full:
        result = await db.execute(
            select(EmailAccount).where(
                EmailAccount.user_id == _current_user.id,
                EmailAccount.is_active.is_(True),
            )
        )
        for account in result.scalars().all():
            account.last_scan_uid = None
        await db.commit()
    options = ScanOptions(
        unread_only=unread_only,
        since=since,
        reset_state=effective_full,
    )
    _ = asyncio.create_task(scan_all_accounts(options=options))
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
    total = (
        await db.execute(
            select(func.count(ScanLog.id)).where(ScanLog.user_id == _current_user.id)
        )
    ).scalar() or 0
    result = await db.execute(
        select(ScanLog)
        .where(ScanLog.user_id == _current_user.id)
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
    assert_owned(await db.get(ScanLog, log_id), _current_user)

    result = await db.execute(
        select(ExtractionLog)
        .where(
            ExtractionLog.user_id == _current_user.id,
            ExtractionLog.scan_log_id == log_id,
        )
        .order_by(ExtractionLog.created_at.asc(), ExtractionLog.id.asc())
    )
    extractions = list(result.scalars().all())
    return ExtractionLogListResponse(items=[_serialize_extraction(item) for item in extractions])


@router.get("/logs/{log_id}/summary", response_model=ExtractionSummaryResponse)
async def scan_log_summary(
    log_id: int,
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> ExtractionSummaryResponse:
    assert_owned(await db.get(ScanLog, log_id), _current_user)

    outcome_rows = await db.execute(
        select(ExtractionLog.outcome, func.count(ExtractionLog.id))
        .where(
            ExtractionLog.user_id == _current_user.id,
            ExtractionLog.scan_log_id == log_id,
        )
        .group_by(ExtractionLog.outcome)
    )
    method_rows = await db.execute(
        select(ExtractionLog.parse_method, func.count(ExtractionLog.id))
        .where(
            ExtractionLog.user_id == _current_user.id,
            ExtractionLog.scan_log_id == log_id,
        )
        .where(ExtractionLog.parse_method.is_not(None))
        .group_by(ExtractionLog.parse_method)
    )
    tier_rows = await db.execute(
        select(ExtractionLog.classification_tier, func.count(ExtractionLog.id))
        .where(
            ExtractionLog.user_id == _current_user.id,
            ExtractionLog.scan_log_id == log_id,
        )
        .where(ExtractionLog.classification_tier.is_not(None))
        .group_by(ExtractionLog.classification_tier)
    )

    outcomes = {row[0]: int(row[1]) for row in outcome_rows.all()}
    parse_methods = {row[0]: int(row[1]) for row in method_rows.all() if row[0]}
    tiers = {f"tier{int(row[0])}": int(row[1]) for row in tier_rows.all() if row[0] is not None}

    return ExtractionSummaryResponse(
        scan_log_id=log_id,
        total=sum(outcomes.values()),
        outcomes=outcomes,
        parse_methods=parse_methods,
        classification_tiers=tiers,
    )
