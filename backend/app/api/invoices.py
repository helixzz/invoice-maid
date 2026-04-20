# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false, reportUntypedFunctionDecorator=false, reportCallInDefaultInitializer=false

from __future__ import annotations

import logging
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

import aiofiles
import filetype
from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.deps import CurrentUser, assert_owned
from app.models import CorrectionLog, Invoice
from app.rate_limiter import limiter
from app.schemas.invoice import InvoiceListResponse, InvoiceResponse
from app.services.ai_service import AIService
from app.services.file_manager import FileManager
from app.services.invoice_csv import CSV_COLUMNS, CSV_UTF8_BOM, build_csv_content
from app.services.manual_upload import UploadResult, process_uploaded_invoice
from app.services.search_service import SearchService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/invoices", tags=["invoices"])

UPLOAD_MAX_BYTES = 25 * 1024 * 1024
UPLOAD_CHUNK_SIZE = 256 * 1024
UPLOAD_ALLOWED_MIME: frozenset[str] = frozenset(
    {
        "application/pdf",
        "application/xml",
        "text/xml",
        "application/octet-stream",
        "application/ofd",
        "application/zip",
        "application/x-zip-compressed",
    }
)
UPLOAD_ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".xml", ".ofd"})
UPLOAD_MAGIC_PREFIXES: dict[str, tuple[bytes, ...]] = {
    "pdf": (b"%PDF",),
    "xml": (b"<?xml", b"\xef\xbb\xbf<?xml"),
    "ofd": (b"PK\x03\x04",),
}


class UploadErrorDetail(BaseModel):
    detail: str
    outcome: str
    invoice_no: str | None = None
    confidence: float | None = None
    existing_invoice_id: int | None = None
    parse_method: str | None = None
    parse_format: str | None = None


def _outcome_to_status(outcome: str) -> int:
    if outcome == "duplicate":
        return status.HTTP_409_CONFLICT
    if outcome in ("low_confidence", "not_vat_invoice", "scam_detected"):
        return status.HTTP_422_UNPROCESSABLE_CONTENT
    if outcome == "parse_failed":
        return status.HTTP_422_UNPROCESSABLE_CONTENT
    return status.HTTP_500_INTERNAL_SERVER_ERROR


def _detect_format_from_magic(head: bytes) -> str | None:
    for fmt, prefixes in UPLOAD_MAGIC_PREFIXES.items():
        for prefix in prefixes:
            if head.startswith(prefix):
                return fmt
    guess = filetype.guess(head)
    if guess is not None and guess.extension in UPLOAD_MAGIC_PREFIXES:
        return guess.extension
    return None


def _safe_filename(client_filename: str | None) -> str:
    """UUID + extension. Never uses client-supplied path components —
    prevents the `../../etc/passwd` class of attack entirely."""
    ext = ""
    if client_filename:
        suffix = Path(client_filename).suffix.lower()
        if suffix in UPLOAD_ALLOWED_EXTENSIONS:
            ext = suffix
    return f"{uuid.uuid4().hex}{ext or '.bin'}"


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


@router.post(
    "/upload",
    response_model=InvoiceResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("30/minute")
async def upload_invoice(
    request: Request,
    response: Response,
    _current_user: CurrentUser,
    file: Annotated[UploadFile, File(description="Single PDF, XML, or OFD invoice (max 25 MB)")],
    db: AsyncSession = Depends(get_db),
) -> InvoiceResponse:
    """Manually upload one invoice file (PDF / XML / OFD) and run it
    through the same parse + LLM extraction pipeline the email scanner
    uses. Designed for historical backlog, paper invoices the user has
    scanned, or invoices received via non-email channels (WeChat etc.).

    Errors are returned as structured JSON with an ``outcome`` field so
    the frontend can give specific feedback:
        duplicate       -> 409 + existing_invoice_id
        low_confidence  -> 422 + confidence
        not_vat_invoice -> 422
        scam_detected   -> 422
        parse_failed    -> 422
        413 / 415       -> file-level rejection before parsing
    """
    del request
    del response

    declared_mime = (file.content_type or "").lower()
    if declared_mime and declared_mime not in UPLOAD_ALLOWED_MIME:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported content-type: {declared_mime!r}",
        )

    ext_from_name = Path(file.filename or "").suffix.lower()
    if ext_from_name and ext_from_name not in UPLOAD_ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported extension: {ext_from_name!r}",
        )

    settings = get_settings()
    file_mgr = FileManager(settings.STORAGE_PATH)
    ai = AIService(settings)

    total = 0
    first_chunk = True
    detected: str | None = None
    chunks: list[bytes] = []
    try:
        while True:
            chunk = await file.read(UPLOAD_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > UPLOAD_MAX_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Upload exceeds {UPLOAD_MAX_BYTES} byte limit",
                )
            if first_chunk:
                detected = _detect_format_from_magic(chunk[:512])
                if detected is None:
                    raise HTTPException(
                        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                        detail=(
                            "File content does not match an accepted format "
                            "(PDF / XML / OFD)"
                        ),
                    )
                first_chunk = False
            chunks.append(chunk)
    finally:
        await file.close()

    if total == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file"
        )

    payload = b"".join(chunks)
    safe_name = _safe_filename(file.filename)
    parse_filename = safe_name
    if detected is not None and not safe_name.endswith(f".{detected}"):
        parse_filename = f"{Path(safe_name).stem}.{detected}"

    try:
        result: UploadResult = await process_uploaded_invoice(
            db=db,
            ai=ai,
            file_mgr=file_mgr,
            settings=settings,
            filename=parse_filename,
            payload=payload,
            user_id=_current_user.id,
        )
    except RuntimeError as exc:
        logger.error("Manual upload precondition failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc

    if result.outcome == "saved" and result.invoice is not None:
        return _serialize_invoice(result.invoice)

    raise HTTPException(
        status_code=_outcome_to_status(result.outcome),
        detail={
            "detail": result.detail,
            "outcome": result.outcome,
            "invoice_no": result.invoice_no,
            "confidence": result.confidence,
            "existing_invoice_id": result.existing_invoice_id,
            "parse_method": result.parse_method,
            "parse_format": result.parse_format,
        },
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
        user_id=_current_user.id,
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
        user_id=_current_user.id,
        date_from=date_from,
        date_to=date_to,
        page=1,
        size=1,
    )
    invoices, _ = await search_service.search_fts(
        db=db,
        query=q,
        user_id=_current_user.id,
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
    invoice = assert_owned(await db.get(Invoice, invoice_id), _current_user)
    return _serialize_invoice(invoice)


@router.get("/{invoice_id}/similar", response_model=list[InvoiceResponse])
async def get_similar_invoices(
    invoice_id: int,
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> list[InvoiceResponse]:
    invoice = assert_owned(await db.get(Invoice, invoice_id), _current_user)

    search_service = SearchService(get_settings())
    similar_ids = await search_service.similar_invoice_ids(db=db, invoice=invoice, limit=5)
    similar_invoices = await search_service.fetch_invoices_by_ids(
        db=db, invoice_ids=similar_ids, user_id=_current_user.id
    )
    return [_serialize_invoice(similar_invoice) for similar_invoice in similar_invoices]


@router.put("/{invoice_id}", response_model=InvoiceResponse)
async def update_invoice(
    invoice_id: int,
    payload: InvoiceUpdateRequest,
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> InvoiceResponse:
    invoice = assert_owned(await db.get(Invoice, invoice_id), _current_user)

    update_data = payload.model_dump(exclude_unset=True)
    if "invoice_no" in update_data:
        duplicate = await db.execute(
            select(Invoice.id).where(
                Invoice.user_id == _current_user.id,
                Invoice.invoice_no == update_data["invoice_no"],
                Invoice.id != invoice_id,
            )
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
                user_id=_current_user.id,
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
    invoice = assert_owned(await db.get(Invoice, invoice_id), _current_user)

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
        user_id=_current_user.id,
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
    result = await db.execute(
        select(Invoice).where(
            Invoice.user_id == _current_user.id,
            Invoice.id.in_(payload.ids),
        )
    )
    invoices = list(result.scalars().all())

    for invoice in invoices:
        file_path = invoice.file_path
        await db.delete(invoice)
        await file_manager.delete_invoice_file(file_path)

    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
