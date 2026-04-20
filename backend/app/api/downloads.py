# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false, reportUntypedFunctionDecorator=false, reportCallInDefaultInitializer=false

from __future__ import annotations

import asyncio
import io

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.deps import CurrentUser
from app.models import Invoice
from app.services.file_manager import FileManager
from app.services.invoice_csv import SUMMARY_FILENAME, build_csv_bytes

router = APIRouter(prefix="/invoices", tags=["downloads"])


class BatchDownloadRequest(BaseModel):
    ids: list[int]


@router.get("/{invoice_id}/download")
async def download_invoice(
    invoice_id: int,
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    invoice = await db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    file_manager = FileManager(get_settings().STORAGE_PATH)
    try:
        full_path = await asyncio.to_thread(file_manager.get_full_path, invoice.file_path)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice file not found") from exc

    if not await asyncio.to_thread(full_path.exists):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice file not found")

    return FileResponse(path=full_path, filename=full_path.name)


@router.post("/batch-download")
async def batch_download_invoices(
    payload: BatchDownloadRequest,
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    if not payload.ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No invoice ids provided")

    result = await db.execute(select(Invoice).where(Invoice.id.in_(payload.ids)))
    invoices = list(result.scalars().all())
    if not invoices:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoices not found")

    summary_bytes = build_csv_bytes(invoices)
    file_paths = [invoice.file_path for invoice in invoices]
    extra_members = [(SUMMARY_FILENAME, summary_bytes)]

    file_manager = FileManager(get_settings().STORAGE_PATH)
    archive = await asyncio.to_thread(file_manager.stream_zip, file_paths, extra_members)

    return StreamingResponse(
        io.BytesIO(archive),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="invoices.zip"'},
    )
