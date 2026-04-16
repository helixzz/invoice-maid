# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false, reportUntypedFunctionDecorator=false, reportCallInDefaultInitializer=false

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.deps import CurrentUser
from app.models import Invoice
from app.schemas.invoice import InvoiceListResponse, InvoiceResponse
from app.services.ai_service import AIService
from app.services.file_manager import FileManager
from app.services.search_service import SearchService

router = APIRouter(prefix="/invoices", tags=["invoices"])


class SemanticSearchRequest(BaseModel):
    query: str


class BatchDeleteRequest(BaseModel):
    ids: list[int]


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
        created_at=invoice.created_at.isoformat(),
    )


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
    embedding = await ai_service.embed_text(payload.query)
    invoices, total = await search_service.search(
        db=db,
        query=payload.query,
        query_embedding=embedding,
        page=1,
        size=20,
    )
    return InvoiceListResponse(
        items=[_serialize_invoice(invoice) for invoice in invoices],
        total=total,
        page=1,
        size=20,
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
