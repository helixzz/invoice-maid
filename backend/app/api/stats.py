from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import CurrentUser
from app.models import EmailAccount, Invoice, ScanLog

router = APIRouter(tags=["stats"])


class StatsResponse(BaseModel):
    total_invoices: int
    total_amount: float
    invoices_this_month: int
    amount_this_month: float
    active_accounts: int
    last_scan_at: str | None
    last_scan_found: int | None


def _month_bounds(today: date) -> tuple[date, date]:
    month_start = today.replace(day=1)
    next_month_start = (month_start + timedelta(days=32)).replace(day=1)
    return month_start, next_month_start


def _to_float(value: Decimal | float | int | None) -> float:
    return float(value or 0)


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> StatsResponse:
    month_start, next_month_start = _month_bounds(date.today())

    total_invoices = (await db.execute(select(func.count(Invoice.id)))).scalar() or 0
    total_amount = (
        await db.execute(select(func.coalesce(func.sum(Invoice.amount), 0)))
    ).scalar()

    invoices_this_month = (
        await db.execute(
            select(func.count(Invoice.id)).where(
                Invoice.invoice_date >= month_start,
                Invoice.invoice_date < next_month_start,
            )
        )
    ).scalar() or 0
    amount_this_month = (
        await db.execute(
            select(func.coalesce(func.sum(Invoice.amount), 0)).where(
                Invoice.invoice_date >= month_start,
                Invoice.invoice_date < next_month_start,
            )
        )
    ).scalar()

    active_accounts = (
        await db.execute(select(func.count(EmailAccount.id)).where(EmailAccount.is_active.is_(True)))
    ).scalar() or 0

    last_scan = (
        await db.execute(select(ScanLog).order_by(ScanLog.started_at.desc(), ScanLog.id.desc()).limit(1))
    ).scalar_one_or_none()

    return StatsResponse(
        total_invoices=total_invoices,
        total_amount=_to_float(total_amount),
        invoices_this_month=invoices_this_month,
        amount_this_month=_to_float(amount_this_month),
        active_accounts=active_accounts,
        last_scan_at=last_scan.started_at.isoformat() if last_scan else None,
        last_scan_found=last_scan.invoices_found if last_scan else None,
    )
