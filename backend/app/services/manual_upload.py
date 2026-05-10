"""Process a user-uploaded invoice through the same parse -> extract -> save
pipeline that the email scanner uses, minus the email-specific prelude
(no classification, no attachment dedup-by-UID, no webhook skip conditions).

Design: instead of extracting the ~265-line per-attachment loop out of
``_process_single_email`` (which would put the 433 passing email-path tests
at regression risk), we re-compose the same public primitives here:

    invoice_parser.parse()  ->  AIService.extract_invoice_fields()
    FileManager.save_invoice()  ->  Invoice row  ->  ExtractionLog row
    + optional embedding and webhook

Outcome strings match the email-path taxonomy (saved / duplicate /
low_confidence / not_vat_invoice / error) plus one new one:
``manual_upload_saved`` for successful manual uploads, so scan-log
queries can distinguish origins via SQL without JOIN gymnastics.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import EmailAccount, ExtractionLog, Invoice, ScanLog
from app.schemas.invoice import InvoiceCategory, TRANSPORT_E_TICKET_TYPES, VALID_INVOICE_TYPES
from app.services.ai_service import AIService
from app.services.email_classifier import is_scam_text
from app.services.file_manager import FileManager
from app.services.invoice_parser import ParsedInvoice
from app.services.invoice_parser import parse as parse_invoice
from app.services.search_service import store_embedding

logger = logging.getLogger(__name__)


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
    """Fetch the sentinel EmailAccount seeded by Alembic migration 0008.
    Raises RuntimeError if the migration hasn't run — this is a hard
    precondition for the upload feature to work at all."""
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
    # Commit immediately so the SQLite writer lock is released before we
    # make the (potentially 10-30 second) LLM enrichment call downstream.
    # Without this commit, three concurrent uploads each hold the writer
    # lock through their LLM round-trip, blocking each other until one
    # hits the 30s busy_timeout — observed as `database is locked` errors
    # in v0.8.7 production logs. See also connect_args timeout=30 in
    # database.py, which is the second half of the fix.
    await db.commit()
    await db.refresh(scan_log)
    return scan_log


def _truncate(text: str | None, limit: int = 500) -> str | None:
    if text is None:
        return None
    return text[:limit]


def _is_transport_e_ticket(parsed: ParsedInvoice) -> bool:
    """True when the parsed invoice is a 2024-era railway or airline
    e-ticket — either by official type name match, or by raw-text markers
    that indicate the document is a 铁路电子客票 / 电子行程单 even when the
    PDF is image-based and type label didn't survive parsing.

    Enables the relaxed amount gate in ``process_uploaded_invoice`` per
    国家税务总局 2024年第8号公告 (railway, 2024-11-01 effective) and
    2024年第9号公告 (airline, 2024-12-01 effective), which classified
    these tickets as full 全面数字化的电子发票 despite often lacking a
    parseable 价税合计 when the PDF is rendered as an image."""
    invoice_type = parsed.invoice_type or ""
    if invoice_type in TRANSPORT_E_TICKET_TYPES:
        return True
    item_summary = parsed.item_summary or ""
    raw_text = parsed.raw_text or ""
    transport_markers = ("铁路电子客票", "铁路客运", "航空运输电子客票行程单", "电子行程单")
    invoice_no = parsed.invoice_no or ""
    has_digital_invoice_no = len(invoice_no) == 20 and invoice_no.isdigit()
    if not has_digital_invoice_no:
        return False
    for marker in transport_markers:
        if marker in invoice_type or marker in item_summary or marker in raw_text:
            return True
    return False


def _should_enrich(parsed: ParsedInvoice) -> tuple[bool, bool]:
    """Return (should_enrich, amount_is_sentinel). Mirrors the decision
    logic from tasks/scheduler.py:428-443 for the email path so manual
    uploads receive the same treatment."""
    amount_is_sentinel = parsed.amount is not None and parsed.amount < Decimal("0.10")
    fields_missing = (
        not parsed.buyer
        or parsed.buyer == "未知"
        or not parsed.seller
        or parsed.seller == "未知"
        or not parsed.invoice_type
        or parsed.invoice_type == "未知"
        or not parsed.item_summary
    )
    should_enrich = (
        parsed.confidence < 0.6
        or not parsed.invoice_no
        or amount_is_sentinel
        or fields_missing
    ) and bool(parsed.raw_text)
    return should_enrich, amount_is_sentinel


def _merge_llm_into_parsed(parsed: ParsedInvoice, extracted: Any) -> bool:
    """Selective merge: LLM fills semantic fields; parser keeps strong
    deterministic invoice_no (QR / XML / OFD / 8-or-20-digit regex).
    Returns amount_is_sentinel after merge."""
    parser_invoice_no_looks_valid = bool(
        parsed.invoice_no
        and parsed.invoice_no.isdigit()
        and len(parsed.invoice_no) in (8, 20)
    )
    if extracted.buyer and extracted.buyer != "未知":
        parsed.buyer = extracted.buyer
    if extracted.seller and extracted.seller != "未知":
        parsed.seller = extracted.seller
    if extracted.item_summary and extracted.item_summary != "未知":
        parsed.item_summary = extracted.item_summary
    if extracted.invoice_type and extracted.invoice_type != "未知":
        parsed.invoice_type = extracted.invoice_type
    parsed.invoice_category = extracted.invoice_category.value
    if not parser_invoice_no_looks_valid and extracted.invoice_no:
        parsed.invoice_no = extracted.invoice_no
    if parsed.invoice_date is None and extracted.invoice_date:
        parsed.invoice_date = extracted.invoice_date
    if parsed.amount is None or (parsed.amount is not None and parsed.amount < Decimal("0.10")):
        if extracted.amount and extracted.amount >= Decimal("0.10"):
            parsed.amount = extracted.amount
    parsed.extraction_method = "llm"
    parsed.confidence = max(extracted.confidence, parsed.confidence)
    amount_is_sentinel = parsed.amount is not None and parsed.amount < Decimal("0.10")
    return amount_is_sentinel


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
    """End-to-end processing of one uploaded file.
    Commits a ScanLog + ExtractionLog + (on success) an Invoice row.
    Never raises for expected outcomes — returns UploadResult instead so
    the endpoint can map each outcome to the right HTTP status."""
    account = await _get_or_fail_manual_account(db)
    scan_log = await _create_upload_scan_log(db, account.id, user_id, filename)
    subject = f"Manual upload: {filename}"

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
                email_subject=subject,
                attachment_filename=filename,
                outcome=outcome,
                classification_tier=None,
                parse_method=parse_method,
                parse_format=parse_format,
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

    parser_invoice_no_looks_valid = bool(
        parsed.invoice_no
        and parsed.invoice_no.isdigit()
        and len(parsed.invoice_no) in (8, 20)
    )
    strong_parse = (
        parsed.extraction_method in ("qr", "xml_xpath", "ofd_struct")
        or parser_invoice_no_looks_valid
    )

    should_enrich, amount_is_sentinel = _should_enrich(parsed)
    extracted: Any = None
    if should_enrich:
        try:
            extracted = await ai.extract_invoice_fields(db, parsed.raw_text)
        except Exception as exc:
            logger.warning(
                "LLM enrichment failed for upload %s (%s); falling back to parser",
                filename,
                exc,
            )
            extracted = None
        if extracted is not None and (
            extracted.is_valid_tax_invoice
            or extracted.invoice_category != InvoiceCategory.VAT_INVOICE
        ):
            amount_is_sentinel = _merge_llm_into_parsed(parsed, extracted)

    scam_text = " ".join(
        s for s in (parsed.buyer, parsed.seller, parsed.item_summary) if s
    )
    scam_hit, scam_reason = is_scam_text(scam_text)
    if scam_hit:
        _log_extraction(
            "not_vat_invoice",
            parse_method=parsed.extraction_method,
            parse_format=parsed.source_format,
            invoice_no=parsed.invoice_no,
            confidence=parsed.confidence,
            error_detail=f"scam signal: {scam_reason}",
        )
        return await _finalize(
            outcome="scam_detected",
            detail=f"Rejected: {scam_reason}",
            invoice_no=parsed.invoice_no,
            confidence=parsed.confidence,
            parse_method=parsed.extraction_method,
            parse_format=parsed.source_format,
        )

    final_type = parsed.invoice_type or ""
    type_is_valid = final_type in VALID_INVOICE_TYPES or any(
        vt in final_type for vt in VALID_INVOICE_TYPES if len(vt) > 3
    )
    is_transport_eticket = _is_transport_e_ticket(parsed)
    llm_rejected = (
        extracted is not None
        and not extracted.is_valid_tax_invoice
        and not strong_parse
        and not is_transport_eticket
    )
    heuristic_rejected = (
        extracted is None
        and not parsed.is_vat_document
        and not type_is_valid
        and not is_transport_eticket
    )
    # v1.2.0 Track A: the rejection rule depends on STRICT_VAT_ONLY.
    # When false (default), non-VAT categories (receipt / proforma /
    # overseas_invoice / other) save normally — only invoice_category=
    # vat_invoice rows with is_valid_tax_invoice=false get rejected.
    # When true, revert to the v1.1.x rule (reject by type + flag).
    # See .sisyphus/plans/v1.2.0-track-a-invoice-category.md §5.2.
    final_category = (
        extracted.invoice_category.value
        if extracted is not None
        else (parsed.invoice_category or "vat_invoice")
    )
    if settings.STRICT_VAT_ONLY:
        should_reject = llm_rejected or (not type_is_valid and heuristic_rejected)
    else:
        should_reject = (
            final_category == "vat_invoice"
            and (llm_rejected or (not type_is_valid and heuristic_rejected))
        )
    if should_reject:
        _log_extraction(
            "not_vat_invoice",
            parse_method=parsed.extraction_method,
            parse_format=parsed.source_format,
            invoice_no=parsed.invoice_no,
            confidence=parsed.confidence,
            error_detail=f"type={final_type!r} category={final_category!r} llm_rejected={llm_rejected}",
        )
        return await _finalize(
            outcome="not_vat_invoice",
            detail=(
                "File does not appear to be a valid VAT invoice "
                f"(type={final_type!r}, category={final_category!r}, llm_rejected={llm_rejected})"
            ),
            invoice_no=parsed.invoice_no,
            confidence=parsed.confidence,
            parse_method=parsed.extraction_method,
            parse_format=parsed.source_format,
        )

    # Confidence + completeness gate. Transport e-tickets get a relaxed
    # amount check: image-based railway/airline ticket PDFs legitimately
    # cannot yield a readable 票价 from pdfplumber/PyMuPDF alone, but their
    # 20-digit invoice_no + invoice_type + 2024年第8号公告 still make them
    # valid VAT invoices. Save with amount=0 so the user can correct later
    # via the inline-edit UI, rather than silently rejecting.
    effective_amount_is_sentinel = amount_is_sentinel and not is_transport_eticket
    effective_confidence_floor = 0.5 if is_transport_eticket else 0.6
    if (
        parsed.confidence < effective_confidence_floor
        or not parsed.invoice_no
        or effective_amount_is_sentinel
    ):
        reason = (
            "amount below sentinel"
            if effective_amount_is_sentinel
            else ("missing invoice_no" if not parsed.invoice_no else "low confidence")
        )
        _log_extraction(
            "low_confidence",
            parse_method=parsed.extraction_method,
            parse_format=parsed.source_format,
            invoice_no=parsed.invoice_no,
            confidence=parsed.confidence,
            error_detail=reason,
        )
        return await _finalize(
            outcome="low_confidence",
            detail=(
                f"Extraction confidence too low "
                f"(confidence={parsed.confidence:.2f}, reason={reason})"
            ),
            invoice_no=parsed.invoice_no,
            confidence=parsed.confidence,
            parse_method=parsed.extraction_method,
            parse_format=parsed.source_format,
        )

    existing_row = await db.execute(
        select(Invoice).where(
            Invoice.user_id == user_id,
            Invoice.invoice_no == parsed.invoice_no,
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

    ext = (
        f".{filename.rsplit('.', 1)[-1].lower()}" if "." in filename else ".pdf"
    )
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
        invoice_date=parsed.invoice_date or datetime.now(timezone.utc).date(),
        invoice_type=parsed.invoice_type or "未知",
        invoice_category=final_category,
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
    try:
        await db.flush()
    except IntegrityError:  # pragma: no cover
        # Race path: another transaction inserted the same invoice_no
        # between our pre-flush SELECT (line ~302 above) and this flush.
        # Structurally identical to tasks/scheduler.py:611-629 in the
        # email scanner, which IS exercised by test_scheduler's
        # concurrent-insert tests — the two share the Invoice.invoice_no
        # UNIQUE constraint that triggers them. Marked no-cover here
        # because reproducing the race in a single-threaded test
        # engine requires nested-session gymnastics that trip SQLAlchemy's
        # greenlet machinery.
        await db.rollback()
        scan_log = await _create_upload_scan_log(db, account.id, user_id, filename)
        dup_row = await db.execute(
            select(Invoice).where(
                Invoice.user_id == user_id,
                Invoice.invoice_no == parsed.invoice_no,
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
            detail=(
                f"Invoice {parsed.invoice_no} already exists "
                "(race condition during upload)"
            ),
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
