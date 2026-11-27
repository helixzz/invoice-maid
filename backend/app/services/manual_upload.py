"""Process a user-uploaded invoice through the shared ingestion pipeline
(``app.services.invoice_ingestion``) so manual uploads, email scans, and SaaS
scrapers enforce identical parsing, LLM/VLM enrichment, multi-currency,
anti-scam, VAT validation, and dedup rules.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import EmailAccount, ExtractionLog, Invoice, ScanLog
from app.models.invoice import invoice_no_dedup_expression
from app.services.ai_service import AIService
from app.services.email_classifier import is_scam_text
from app.services.file_manager import FileManager
from app.services.invoice_ingestion import (
    enrich_and_validate_invoice,
    is_transport_e_ticket as _is_transport_e_ticket,
    merge_llm_into_parsed as _merge_llm_into_parsed,
)
from app.services.invoice_parser import invoice_no_dedup_key
from app.services.invoice_parser import parse as parse_invoice
from app.services.search_service import store_embedding

logger = logging.getLogger(__name__)

# Re-exported for backwards compatibility with existing unit tests.
__all__ = [
    "MANUAL_ACCOUNT_TYPE",
    "UploadOutcome",
    "UploadResult",
    "_is_transport_e_ticket",
    "_merge_llm_into_parsed",
    "process_uploaded_invoice",
]

MANUAL_ACCOUNT_TYPE = "manual"

UploadOutcome = Literal[
    "saved",
    "duplicate",
    "low_confidence",
    "not_vat_invoice",
    "parse_failed",
    "scam_detected",
    "error",
]


@dataclass
class UploadResult:
    outcome: UploadOutcome
    detail: str
    invoice: Invoice | None = None
    existing_invoice_id: int | None = None
    confidence: float | None = None
    invoice_no: str | None = None
    parse_method: str | None = None
    parse_format: str | None = None


async def _get_or_fail_manual_account(db: AsyncSession) -> EmailAccount:
    result = await db.execute(
        select(EmailAccount).where(EmailAccount.type == MANUAL_ACCOUNT_TYPE).limit(1)
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise RuntimeError(
            "Manual-upload pseudo account missing; run `alembic upgrade head` "
            "to apply migration 0008_manual_upload_pseudo_account."
        )
    return account


async def _create_upload_scan_log(
    db: AsyncSession, account_id: int, user_id: int, filename: str
) -> ScanLog:
    del filename
    scan_log = ScanLog(
        user_id=user_id,
        email_account_id=account_id,
        started_at=datetime.now(timezone.utc),
        emails_scanned=1,
        invoices_found=0,
        error_message=None,
    )
    db.add(scan_log)
    await db.commit()
    await db.refresh(scan_log)
    return scan_log


def _truncate(text: str | None, limit: int = 500) -> str | None:
    if text is None:
        return None
    return text[:limit]


async def process_uploaded_invoice(
    *,
    db: AsyncSession,
    ai: AIService,
    file_mgr: FileManager,
    settings: Settings,
    filename: str,
    payload: bytes,
    user_id: int,
) -> UploadResult:
    """Parse, enrich (LLM or VLM fallback), validate, dedup, and persist an uploaded invoice."""
    account = await _get_or_fail_manual_account(db)
    scan_log = await _create_upload_scan_log(db, account.id, user_id, filename)

    def _log_extraction(
        outcome: str,
        *,
        parse_method: str | None = None,
        parse_format: str | None = None,
        invoice_no: str | None = None,
        confidence: float | None = None,
        error_detail: str | None = None,
    ) -> None:
        db.add(
            ExtractionLog(
                user_id=user_id,
                scan_log_id=scan_log.id,
                email_uid=None,
                email_subject=f"[Manual Upload] {filename}"[:500],
                attachment_filename=filename[:500],
                outcome=outcome,
                classification_tier=None,
                parse_method=parse_method,
                parse_format=parse_format,
                download_outcome=None,
                invoice_no=invoice_no,
                confidence=confidence,
                error_detail=_truncate(error_detail, 2000),
            )
        )

    async def _finalize(
        *,
        outcome: UploadOutcome,
        detail: str,
        invoice: Invoice | None = None,
        existing_invoice_id: int | None = None,
        invoice_no: str | None = None,
        confidence: float | None = None,
        parse_method: str | None = None,
        parse_format: str | None = None,
    ) -> UploadResult:
        scan_log.finished_at = datetime.now(timezone.utc)
        scan_log.invoices_found = 1 if invoice is not None else 0
        await db.commit()
        return UploadResult(
            outcome=outcome,
            detail=detail,
            invoice=invoice,
            existing_invoice_id=existing_invoice_id,
            confidence=confidence,
            invoice_no=invoice_no,
            parse_method=parse_method,
            parse_format=parse_format,
        )

    try:
        parsed = await asyncio.to_thread(parse_invoice, filename, payload)
    except Exception as exc:
        logger.warning("Manual upload parse failed for %s: %s", filename, exc)
        _log_extraction("parse_failed", error_detail=f"parse error: {exc}")
        return await _finalize(
            outcome="parse_failed", detail=f"Could not parse file: {exc}"
        )

    verdict = await enrich_and_validate_invoice(
        db=db,
        ai=ai,
        settings=settings,
        parsed=parsed,
        payload=payload,
        filename=filename,
        allow_transport_relaxation=True,
        scam_checker=is_scam_text,
    )
    if not verdict.accepted:
        assert verdict.rejection_outcome is not None
        assert verdict.extraction_log_outcome is not None
        _log_extraction(
            verdict.extraction_log_outcome,
            parse_method=parsed.extraction_method,
            parse_format=parsed.source_format,
            invoice_no=parsed.invoice_no,
            confidence=parsed.confidence,
            error_detail=verdict.reason,
        )
        return await _finalize(
            outcome=verdict.rejection_outcome,
            detail=verdict.detail,
            invoice_no=parsed.invoice_no,
            confidence=parsed.confidence,
            parse_method=parsed.extraction_method,
            parse_format=parsed.source_format,
        )

    existing_row = await db.execute(
        select(Invoice).where(
            Invoice.user_id == user_id,
            invoice_no_dedup_expression() == invoice_no_dedup_key(parsed.invoice_no),
        )
    )
    existing_invoice = existing_row.scalar_one_or_none()
    if existing_invoice is not None:
        _log_extraction(
            "duplicate",
            parse_method=parsed.extraction_method,
            parse_format=parsed.source_format,
            invoice_no=parsed.invoice_no,
        )
        return await _finalize(
            outcome="duplicate",
            detail=f"Invoice {parsed.invoice_no} already exists",
            existing_invoice_id=existing_invoice.id,
            invoice_no=parsed.invoice_no,
            parse_method=parsed.extraction_method,
            parse_format=parsed.source_format,
        )

    ext = f".{filename.rsplit('.', 1)[-1].lower()}" if "." in filename else ".pdf"
    file_path = await file_mgr.save_invoice(
        payload,
        parsed.buyer,
        parsed.seller,
        parsed.invoice_no,
        parsed.invoice_date,
        parsed.amount,
        ext,
        user_id=user_id,
    )
    invoice = Invoice(
        user_id=user_id,
        invoice_no=parsed.invoice_no,
        buyer=parsed.buyer or "未知",
        seller=parsed.seller or "未知",
        amount=parsed.amount or 0,
        currency=verdict.final_currency,
        invoice_date=parsed.invoice_date or datetime.now(timezone.utc).date(),
        invoice_type=parsed.invoice_type or "未知",
        invoice_category=verdict.final_category,
        item_summary=parsed.item_summary or "",
        file_path=file_path,
        raw_text=parsed.raw_text[:10000],
        email_uid=f"manual:{scan_log.id}",
        email_account_id=account.id,
        source_format=parsed.source_format,
        extraction_method=parsed.extraction_method,
        confidence=parsed.confidence,
    )
    db.add(invoice)
    from app.services.maid_agent import apply_maid_memories_to_invoice

    await apply_maid_memories_to_invoice(db, invoice)
    try:
        await db.flush()
    except IntegrityError:  # pragma: no cover
        await db.rollback()
        scan_log = await _create_upload_scan_log(db, account.id, user_id, filename)
        dup_row = await db.execute(
            select(Invoice).where(
                Invoice.user_id == user_id,
                invoice_no_dedup_expression() == invoice_no_dedup_key(parsed.invoice_no),
            )
        )
        dup_invoice = dup_row.scalar_one_or_none()
        _log_extraction(
            "duplicate",
            parse_method=parsed.extraction_method,
            parse_format=parsed.source_format,
            invoice_no=parsed.invoice_no,
        )
        return await _finalize(
            outcome="duplicate",
            detail=f"Invoice {parsed.invoice_no} already exists (race condition during upload)",
            existing_invoice_id=dup_invoice.id if dup_invoice is not None else None,
            invoice_no=parsed.invoice_no,
            parse_method=parsed.extraction_method,
            parse_format=parsed.source_format,
        )

    _log_extraction(
        "manual_upload_saved",
        parse_method=parsed.extraction_method,
        parse_format=parsed.source_format,
        invoice_no=parsed.invoice_no,
        confidence=parsed.confidence,
    )

    if settings.sqlite_vec_available:
        try:
            search_text = f"{parsed.buyer} {parsed.seller} {parsed.item_summary or ''}"
            embedding = await ai.embed_text(search_text, db)
            await store_embedding(db, invoice.id, embedding)
        except Exception as exc:
            logger.warning(
                "Embedding failed for manual upload %s: %s", parsed.invoice_no, exc
            )

    return await _finalize(
        outcome="saved",
        detail=f"Invoice {parsed.invoice_no} saved",
        invoice=invoice,
        invoice_no=parsed.invoice_no,
        confidence=parsed.confidence,
        parse_method=parsed.extraction_method,
        parse_format=parsed.source_format,
    )
