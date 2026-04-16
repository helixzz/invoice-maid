# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false, reportUntypedFunctionDecorator=false, reportCallInDefaultInitializer=false

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import CurrentUser
from app.models import ScanLog
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


@router.post("/trigger", response_model=ScanTriggerResponse)
async def trigger_scan(_current_user: CurrentUser) -> ScanTriggerResponse:
    _ = asyncio.create_task(scan_all_accounts())
    return ScanTriggerResponse(status="triggered")


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
