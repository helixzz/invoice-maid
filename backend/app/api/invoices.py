# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false, reportUntypedFunctionDecorator=false, reportCallInDefaultInitializer=false

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.deps import CurrentUser
from app.models import CorrectionLog, Invoice
from app.schemas.invoice import InvoiceListResponse, InvoiceResponse
from app.services.ai_service import AIService
from app.services.file_manager import FileManager
from app.services.invoice_csv import CSV_COLUMNS, CSV_UTF8_BOM, build_csv_content
from app.services.search_service import SearchService

router = APIRouter(prefix="/invoices", tags=["invoices"])


class SemanticSearchRequest(BaseModel):
    query: str
    page: int = Field(default=1, ge=1)
    size: int = Field(default=20, ge=1, le=100)


class BatchDeleteRequest(BaseModel):
    ids: list[int]


class InvoiceUpdateRequest(BaseModel):
    buyer: str | None = None
    seller: str | None = None
    amount: Decimal | None = None
    invoice_date: date | None = None
    invoice_type: str | None = None
    item_summary: str | None = None
    invoice_no: str | None = None


def _stringify_field_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _serialize_invoice(invoice: Invoice) -> InvoiceResponse:
    return InvoiceResponse(
        id=invoice.id,
        invoice_no=invoice.invoice_no,
        buyer=invoice.buyer,
        seller=invoice.seller,
        amount=float(invoice.amount),
        invoice_date=invoice.invoice_date.isoformat(),
        invoice_type=invoice.invoice_type,
        item_summary=invoice.item_summary,
        source_format=invoice.source_format,
        extraction_method=invoice.extraction_method,
        confidence=invoice.confidence,
        is_manually_corrected=invoice.is_manually_corrected,
        created_at=invoice.created_at.isoformat(),
    )


def _build_csv_content(invoices: list[Invoice]) -> str:
    return build_csv_content(invoices)


@router.get("", response_model=InvoiceListResponse)
async def list_invoices(
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    q: str = "",
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = 1,
    size: int = 20,
) -> InvoiceListResponse:
    search_service = SearchService(get_settings())
    invoices, total = await search_service.search_fts(
        db=db,
        query=q,
        date_from=date_from,
        date_to=date_to,
        page=page,
        size=size,
    )
    return InvoiceListResponse(
        items=[_serialize_invoice(invoice) for invoice in invoices],
        total=total,
        page=page,
        size=size,
    )


@router.get("/export")
async def export_invoices(
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    format: Literal["csv"] = "csv",
    q: str = "",
    date_from: date | None = None,
    date_to: date | None = None,
) -> StreamingResponse:
    del format
    search_service = SearchService(get_settings())
    _, total = await search_service.search_fts(
        db=db,
        query=q,
        date_from=date_from,
        date_to=date_to,
        page=1,
        size=1,
    )
    invoices, _ = await search_service.search_fts(
        db=db,
        query=q,
        date_from=date_from,
        date_to=date_to,
        page=1,
        size=max(total, 1),
    )
    csv_content = _build_csv_content(invoices)

    def stream_csv() -> list[bytes]:
        return [CSV_UTF8_BOM + csv_content.encode("utf-8")]

    return StreamingResponse(
        content=stream_csv(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="invoices.csv"'},
    )


@router.get("/{invoice_id}", response_model=InvoiceResponse)
async def get_invoice(
    invoice_id: int,
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> InvoiceResponse:
    invoice = await db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    return _serialize_invoice(invoice)


@router.get("/{invoice_id}/similar", response_model=list[InvoiceResponse])
async def get_similar_invoices(
    invoice_id: int,
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> list[InvoiceResponse]:
    invoice = await db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    search_service = SearchService(get_settings())
    similar_ids = await search_service.similar_invoice_ids(db=db, invoice=invoice, limit=5)
    similar_invoices = await search_service.fetch_invoices_by_ids(db=db, invoice_ids=similar_ids)
    return [_serialize_invoice(similar_invoice) for similar_invoice in similar_invoices]


@router.put("/{invoice_id}", response_model=InvoiceResponse)
async def update_invoice(
    invoice_id: int,
    payload: InvoiceUpdateRequest,
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> InvoiceResponse:
    invoice = await db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    update_data = payload.model_dump(exclude_unset=True)
    if "invoice_no" in update_data:
        duplicate = await db.execute(
            select(Invoice.id).where(Invoice.invoice_no == update_data["invoice_no"], Invoice.id != invoice_id)
        )
        if duplicate.scalar_one_or_none() is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invoice number already exists")

    changed = False
    for field_name, new_value in update_data.items():
        old_value = getattr(invoice, field_name)
        if old_value == new_value:
            continue
        setattr(invoice, field_name, new_value)
        db.add(
            CorrectionLog(
                invoice_id=invoice.id,
                field_name=field_name,
                old_value=_stringify_field_value(old_value),
                new_value=_stringify_field_value(new_value),
            )
        )
        changed = True

    if changed:
        invoice.is_manually_corrected = True

    await db.commit()
    await db.refresh(invoice)
    return _serialize_invoice(invoice)


@router.delete("/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invoice(
    invoice_id: int,
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> Response:
    invoice = await db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    file_path = invoice.file_path
    await db.delete(invoice)
    await db.commit()

    file_manager = FileManager(get_settings().STORAGE_PATH)
    await file_manager.delete_invoice_file(file_path)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/search", response_model=InvoiceListResponse)
async def semantic_search_invoices(
    payload: SemanticSearchRequest,
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> InvoiceListResponse:
    settings = get_settings()
    ai_service = AIService(settings)
    search_service = SearchService(settings)
    embedding = await ai_service.embed_text(payload.query, db)
    invoices, total = await search_service.search(
        db=db,
        query=payload.query,
        query_embedding=embedding,
        page=payload.page,
        size=payload.size,
    )
    return InvoiceListResponse(
        items=[_serialize_invoice(invoice) for invoice in invoices],
        total=total,
        page=payload.page,
        size=payload.size,
    )


@router.post("/batch-delete", status_code=status.HTTP_204_NO_CONTENT)
async def batch_delete_invoices(
    payload: BatchDeleteRequest,
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> Response:
    if not payload.ids:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    file_manager = FileManager(get_settings().STORAGE_PATH)
    invoices = [await db.get(Invoice, invoice_id) for invoice_id in payload.ids]

    for invoice in invoices:
        if invoice is None:
            continue
        file_path = invoice.file_path
        await db.delete(invoice)
        await file_manager.delete_invoice_file(file_path)

    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
