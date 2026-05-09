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
    monthly_spend: list["MonthlySpendPoint"]
    top_sellers: list["SellerSpendPoint"]
    by_type: list["TypeCountPoint"]
    by_category: list["CategoryCountPoint"]
    by_method: list["MethodCountPoint"]
    avg_confidence: float


class MonthlySpendPoint(BaseModel):
    month: str
    total: float
    count: int


class SellerSpendPoint(BaseModel):
    seller: str
    total: float
    count: int


class TypeCountPoint(BaseModel):
    type: str
    count: int


class CategoryCountPoint(BaseModel):
    category: str
    count: int


class MethodCountPoint(BaseModel):
    method: str
    count: int


def _month_bounds(today: date) -> tuple[date, date]:
    month_start = today.replace(day=1)
    next_month_start = (month_start + timedelta(days=32)).replace(day=1)
    return month_start, next_month_start


def _to_float(value: Decimal | float | int | None) -> float:
    return float(value or 0)


def _rounded_float(value: Decimal | float | int | None, digits: int = 6) -> float:
    return round(_to_float(value), digits)


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> StatsResponse:
    month_start, next_month_start = _month_bounds(date.today())
    user_id = _current_user.id

    total_invoices = (
        await db.execute(
            select(func.count(Invoice.id)).where(Invoice.user_id == user_id)
        )
    ).scalar() or 0
    total_amount = (
        await db.execute(
            select(func.coalesce(func.sum(Invoice.amount), 0)).where(
                Invoice.user_id == user_id
            )
        )
    ).scalar()

    invoices_this_month = (
        await db.execute(
            select(func.count(Invoice.id)).where(
                Invoice.user_id == user_id,
                Invoice.invoice_date >= month_start,
                Invoice.invoice_date < next_month_start,
            )
        )
    ).scalar() or 0
    amount_this_month = (
        await db.execute(
            select(func.coalesce(func.sum(Invoice.amount), 0)).where(
                Invoice.user_id == user_id,
                Invoice.invoice_date >= month_start,
                Invoice.invoice_date < next_month_start,
            )
        )
    ).scalar()

    active_accounts = (
        await db.execute(
            select(func.count(EmailAccount.id)).where(
                EmailAccount.user_id == user_id,
                EmailAccount.is_active.is_(True),
            )
        )
    ).scalar() or 0

    last_scan = (
        await db.execute(
            select(ScanLog)
            .where(ScanLog.user_id == user_id)
            .order_by(ScanLog.started_at.desc(), ScanLog.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    monthly_spend_rows = (
        await db.execute(
            select(
                func.strftime("%Y-%m", Invoice.invoice_date).label("month"),
                func.coalesce(func.sum(Invoice.amount), 0).label("total"),
                func.count(Invoice.id).label("count"),
            )
            .where(Invoice.user_id == user_id)
            .group_by("month")
            .order_by("month")
        )
    ).all()
    top_seller_rows = (
        await db.execute(
            select(
                Invoice.seller.label("seller"),
                func.coalesce(func.sum(Invoice.amount), 0).label("total"),
                func.count(Invoice.id).label("count"),
            )
            .where(Invoice.user_id == user_id)
            .group_by(Invoice.seller)
            .order_by(func.sum(Invoice.amount).desc(), func.count(Invoice.id).desc(), Invoice.seller.asc())
            .limit(10)
        )
    ).all()
    type_rows = (
        await db.execute(
            select(Invoice.invoice_type.label("type"), func.count(Invoice.id).label("count"))
            .where(Invoice.user_id == user_id)
            .group_by(Invoice.invoice_type)
            .order_by(func.count(Invoice.id).desc())
        )
    ).all()
    category_rows = (
        await db.execute(
            select(Invoice.invoice_category.label("category"), func.count(Invoice.id).label("count"))
            .where(Invoice.user_id == user_id)
            .group_by(Invoice.invoice_category)
            .order_by(func.count(Invoice.id).desc())
        )
    ).all()
    method_rows = (
        await db.execute(
            select(Invoice.extraction_method.label("method"), func.count(Invoice.id).label("count"))
            .where(Invoice.user_id == user_id)
            .group_by(Invoice.extraction_method)
            .order_by(func.count(Invoice.id).desc(), Invoice.extraction_method.asc())
        )
    ).all()
    avg_confidence = (
        await db.execute(
            select(func.avg(Invoice.confidence)).where(Invoice.user_id == user_id)
        )
    ).scalar()

    return StatsResponse(
        total_invoices=total_invoices,
        total_amount=_to_float(total_amount),
        invoices_this_month=invoices_this_month,
        amount_this_month=_to_float(amount_this_month),
        active_accounts=active_accounts,
        last_scan_at=last_scan.started_at.isoformat() if last_scan else None,
        last_scan_found=last_scan.invoices_found if last_scan else None,
        monthly_spend=[
            MonthlySpendPoint(month=row.month, total=_to_float(row.total), count=row.count)
            for row in monthly_spend_rows
        ],
        top_sellers=[
            SellerSpendPoint(seller=row.seller, total=_to_float(row.total), count=row.count)
            for row in top_seller_rows
        ],
        by_type=[
            TypeCountPoint(type=row.type, count=row.count)
            for row in sorted(type_rows, key=lambda row: row.type, reverse=True)
        ],
        by_category=[
            CategoryCountPoint(category=row.category, count=row.count)
            for row in category_rows
        ],
        by_method=[MethodCountPoint(method=row.method, count=row.count) for row in method_rows],
        avg_confidence=_rounded_float(avg_confidence),
    )
