# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false, reportOptionalMemberAccess=false

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
import httpx
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.config import Settings, get_settings
from app.database import get_db
from app.models import AppSettings, EmailAccount, ExtractionLog, Invoice, ScanLog, WebhookLog
from app.schemas.invoice import InvoiceCategory, VALID_INVOICE_TYPES
from app.services.ai_service import AIService, _resolve_safelink
from app.services.email_classifier import EmailClassifier, _parse_extra_keywords, _parse_trusted_senders, is_scam_text
from app.services.email_scanner import ScanOptions, ScannerFactory, _is_uid_newer
from app.services.file_manager import FileManager
from app.services.invoice_parser import parse as parse_invoice
from app.services.scrapers.factory import ScraperFactory
from app.services.search_service import store_embedding
from app.services import scan_progress as sp

logger = logging.getLogger(__name__)

EMAIL_CONCURRENCY = 50
_email_semaphore = asyncio.Semaphore(EMAIL_CONCURRENCY)

HYDRATION_CONCURRENCY = 5
_hydration_semaphore = asyncio.Semaphore(HYDRATION_CONCURRENCY)

LINK_HOST_BLOCKLIST = frozenset({
    "linktrace.triggerdelivery.com",
    "click.linksynergy.com",
    "beacon.mailchimp.com",
    "trk.klclick.com",
    "t.e.apple.com",
    "click.e.usps.com",
    "email.analytics",
    "click.mail",
    "tracking.pixel",
})

LINK_PATH_BLOCKLIST_SUBSTRINGS = frozenset({
    "/unsubscribe",
    "/track/",
    "/trk/",
    "/open/",
    "/click?",
    "/beacon",
    "/pixel",
})

ACCEPTABLE_INVOICE_CONTENT_TYPES = frozenset({
    "application/pdf",
    "application/octet-stream",
    "application/xml",
    "text/xml",
    "application/zip",
    "application/x-zip-compressed",
    "application/ofd",
})


def _is_blocked_download_url(url: str) -> bool:
    """Filter URLs that are almost certainly tracking pixels, unsubscribe links,
    or analytics beacons rather than invoice documents. Avoids spending a download
    round-trip + LLM extraction + partial PDF-parse attempt on content we know
    is not an invoice."""
    lowered = url.lower()
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
    except ValueError:  # pragma: no cover
        return True
    host = (parsed.netloc or "").lower()
    for blocked_host in LINK_HOST_BLOCKLIST:
        if blocked_host in host:
            return True
    path_and_query = (parsed.path or "") + "?" + (parsed.query or "")
    for blocked_path in LINK_PATH_BLOCKLIST_SUBSTRINGS:
        if blocked_path in path_and_query.lower():
            return True
    if lowered.endswith((".gif", ".jpg", ".jpeg", ".png", ".webp", ".ico", ".svg")):
        return True
    return False


@dataclass
class _EmailResult:
    invoices_added: int = 0
    last_uid: str | None = None
    error: str | None = None


_scheduler: AsyncIOScheduler | None = None


def _guess_filename_from_link(url: str, content_type: str | None) -> str:
    suffix = Path(url.split("?", 1)[0]).suffix
    if suffix in {".pdf", ".xml", ".ofd"}:
        return f"download{suffix}"
    if content_type:
        lowered = content_type.lower()
        if "pdf" in lowered:
            return "download.pdf"
        if "xml" in lowered:
            return "download.xml"
        if "ofd" in lowered or "zip" in lowered:
            return "download.ofd"
    return "download.pdf"


async def _download_linked_invoice(url: str) -> tuple[str, bytes] | None:
    if _is_blocked_download_url(url):
        logger.info("Blocked non-invoice URL (tracker/beacon/unsubscribe): %s", url)
        return None
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            content_type_raw = (response.headers.get("content-type") or "").lower()
            content_type = content_type_raw.split(";", 1)[0].strip()
            if content_type and content_type not in ACCEPTABLE_INVOICE_CONTENT_TYPES:
                logger.info(
                    "Rejected download %s: Content-Type=%r is not an invoice file format",
                    url,
                    content_type,
                )
                return None
            filename = _guess_filename_from_link(url, response.headers.get("content-type"))
            return filename, response.content
    except Exception as exc:
        logger.warning("Failed to download invoice link %s: %s", url, exc)
        return None


def _prioritize_raw_items(items: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    """Process PDFs first (>90% of real invoices), then OFD, then XML, then others."""

    def priority(item: tuple[str, bytes]) -> tuple[int, int]:
        filename = item[0].lower()
        if filename.endswith(".pdf"):
            base = 0
        elif filename.endswith(".ofd"):
            base = 1
        elif filename.endswith(".xml"):
            base = 2
        else:
            base = 3
        return (base, 0)

    return sorted(items, key=priority)


def _prioritize_raw_items_with_hints(
    items: list[tuple[str, bytes]], likely_formats: list[Any] | None
) -> list[tuple[str, bytes]]:
    if not likely_formats:
        return _prioritize_raw_items(items)

    format_priority = {
        str(getattr(fmt, "value", fmt)).lower(): idx for idx, fmt in enumerate(likely_formats)
    }

    def priority(item: tuple[str, bytes]) -> tuple[int, int]:
        filename = item[0].lower()
        ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
        fallback = 3
        if filename.endswith(".pdf"):
            fallback = 0
        elif filename.endswith(".ofd"):
            fallback = 1
        elif filename.endswith(".xml"):
            fallback = 2
        hinted = format_priority.get(ext, len(format_priority) + fallback)
        return (hinted, fallback)

    return sorted(items, key=priority)


def _truncate_error_detail(error_detail: str | None) -> str | None:
    if error_detail is None:
        return None
    return error_detail[:2000]


def _record_extraction_log(
    *,
    user_id: int,
    scan_log_id: int,
    email_uid: str | None,
    email_subject: str,
    attachment_filename: str | None,
    outcome: str,
    classification_tier: int | None = None,
    parse_method: str | None = None,
    parse_format: str | None = None,
    download_outcome: str | None = None,
    invoice_no: str | None = None,
    confidence: float | None = None,
    error_detail: str | None = None,
) -> ExtractionLog:
    return ExtractionLog(
        user_id=user_id,
        scan_log_id=scan_log_id,
        email_uid=email_uid,
        email_subject=email_subject,
        attachment_filename=attachment_filename,
        outcome=outcome,
        classification_tier=classification_tier,
        parse_method=parse_method,
        parse_format=parse_format,
        download_outcome=download_outcome,
        invoice_no=invoice_no,
        confidence=confidence,
        error_detail=_truncate_error_detail(error_detail),
    )


async def _load_classifier(db: Any) -> EmailClassifier:
    trusted_raw = (
        await db.execute(select(AppSettings.value).where(AppSettings.key == "classifier_trusted_senders"))
    ).scalar_one_or_none() or ""
    keywords_raw = (
        await db.execute(select(AppSettings.value).where(AppSettings.key == "classifier_extra_keywords"))
    ).scalar_one_or_none() or ""
    return EmailClassifier(
        trusted_senders=_parse_trusted_senders(trusted_raw),
        extra_keywords=_parse_extra_keywords(keywords_raw),
    )


async def _was_attachment_seen(db: Any, email_uid: str | None, filename: str) -> bool:
    if not email_uid:
        return False

    result = await db.execute(
        select(ExtractionLog.id)
        .where(ExtractionLog.email_uid == email_uid, ExtractionLog.attachment_filename == filename)
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


def _serialize_webhook_amount(amount: Decimal | int | float) -> str:
    return str(amount)


def _build_webhook_payload(invoice: Invoice) -> dict[str, str | float]:
    return {
        "event": "invoice.created",
        "invoice_no": invoice.invoice_no,
        "buyer": invoice.buyer,
        "seller": invoice.seller,
        "amount": _serialize_webhook_amount(invoice.amount),
        "invoice_date": invoice.invoice_date.isoformat(),
        "invoice_type": invoice.invoice_type,
        "confidence": invoice.confidence,
    }


def _sign_webhook_payload(payload: dict[str, str | float], secret: str) -> str:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def _send_invoice_webhook(db, settings: Settings, invoice: Invoice) -> None:
    if not settings.WEBHOOK_URL:
        return

    payload = _build_webhook_payload(invoice)
    headers = {"X-Signature-256": _sign_webhook_payload(payload, settings.WEBHOOK_SECRET)}
    status_code: int | None = None
    success = False
    error_detail: str | None = None

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(settings.WEBHOOK_URL, json=payload, headers=headers)
        status_code = response.status_code
        success = response.is_success
        if not success:
            error_detail = response.text[:2000]
            logger.warning(
                "Webhook delivery failed for invoice %s with status %s",
                invoice.invoice_no,
                status_code,
            )
    except Exception as exc:
        error_detail = str(exc)[:2000]
        logger.warning("Webhook delivery failed for invoice %s: %s", invoice.invoice_no, exc)

    db.add(
        WebhookLog(
            user_id=invoice.user_id,
            event="invoice.created",
            invoice_no=invoice.invoice_no,
            url=settings.WEBHOOK_URL,
            status_code=status_code,
            success=success,
            error_detail=error_detail,
        )
    )
    await db.commit()


async def _process_single_email(
    email_data: Any,
    classifier: Any,
    ai: AIService,
    file_mgr: FileManager,
    settings: Settings,
    log_id: int,
    account_id: int,
    user_id: int,
    scanner: Any = None,
    account: Any = None,
) -> _EmailResult:
    """Process one email with its own DB session. Safe for concurrent execution."""
    async with _email_semaphore:
        async for db in get_db():
            result = _EmailResult(last_uid=email_data.uid)
            try:
                if (
                    scanner is not None
                    and account is not None
                    and not getattr(email_data, "is_hydrated", True)
                ):
                    async with _hydration_semaphore:
                        email_data = await scanner.hydrate_email(account, email_data)

                t1 = classifier.classify_tier1(email_data)

                if t1 is not None:
                    is_invoice = t1.is_invoice
                    classification_tier = 1
                    if is_invoice and email_data.body_links:
                        analysis = await ai.analyze_email(
                            db,
                            subject=email_data.subject,
                            from_addr=email_data.from_addr,
                            body=email_data.body_text,
                            body_links=email_data.body_links,
                        )
                    else:
                        analysis = None
                else:
                    analysis = await ai.analyze_email(
                        db,
                        subject=email_data.subject,
                        from_addr=email_data.from_addr,
                        body=email_data.body_text,
                        body_links=email_data.body_links,
                    )
                    is_invoice = analysis.is_invoice_related
                    classification_tier = 3

                if not is_invoice:
                    db.add(
                        _record_extraction_log(
                            user_id=user_id,
                            scan_log_id=log_id,
                            email_uid=email_data.uid,
                            email_subject=email_data.subject,
                            attachment_filename=None,
                            outcome="not_invoice",
                            classification_tier=classification_tier,
                        )
                    )
                    await db.commit()
                    return result

                raw_items: list[tuple[str, bytes]] = [
                    (att.filename, att.payload)
                    for att in email_data.attachments
                    if att.payload is not None
                ]
                if analysis is not None and analysis.should_download:
                    url = analysis.best_download_url
                    assert url is not None
                    if analysis.url_is_safelink:
                        url = await _resolve_safelink(url)
                    downloaded = await _download_linked_invoice(url)
                    if downloaded is not None:
                        raw_items.append(downloaded)

                raw_items = _prioritize_raw_items_with_hints(
                    raw_items,
                    analysis.extraction_hints.likely_formats if analysis is not None else None,
                )

                for filename, payload in raw_items:
                    if await _was_attachment_seen(db, email_data.uid, filename):
                        db.add(
                            _record_extraction_log(
                                user_id=user_id,
                                scan_log_id=log_id,
                                email_uid=email_data.uid,
                                email_subject=email_data.subject,
                                attachment_filename=filename,
                                outcome="skipped_seen",
                                classification_tier=classification_tier,
                            )
                        )
                        continue

                    try:
                        parsed = await asyncio.to_thread(parse_invoice, filename, payload)
                        extracted = None

                        # Strong parse = deterministic structured extraction succeeded.
                        # We trust these for invoice_no/amount/date and ignore LLM rejection.
                        parser_invoice_no_looks_valid = bool(
                            parsed.invoice_no
                            and parsed.invoice_no.isdigit()
                            and len(parsed.invoice_no) in (8, 20)
                        )
                        strong_parse = (
                            parsed.extraction_method in ("qr", "xml_xpath", "ofd_struct")
                            or parser_invoice_no_looks_valid
                        )

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
                        ) and parsed.raw_text

                        if should_enrich:
                            try:
                                # Prepend email body context so the LLM sees
                                # structured hints (Provider:/Bill-to:/currency)
                                # before the raw parsed text. Critical for
                                # scraper-generated RawEmails where the PDF text
                                # alone carries no category signal.
                                enriched_text = parsed.raw_text
                                if email_data.body_text:
                                    enriched_text = (
                                        f"[Email context]\n"
                                        f"{email_data.body_text}\n"
                                        f"[/Email context]\n\n"
                                        f"[Attachment text]\n"
                                        f"{parsed.raw_text}\n"
                                        f"[/Attachment text]"
                                    )
                                extracted = await ai.extract_invoice_fields(db, enriched_text)
                            except Exception as exc:
                                extracted = None
                                logger.warning(
                                    "LLM enrichment failed for %s (%s); falling back to parser result",
                                    filename,
                                    exc,
                                )

                            if extracted is not None and (
                                extracted.is_valid_tax_invoice
                                or extracted.invoice_category != InvoiceCategory.VAT_INVOICE
                            ):
                                # Selective merge: LLM fills semantic fields (buyer/seller/type/summary)
                                # unconditionally when non-未知. Parser keeps invoice_no/amount/date when
                                # it had strong deterministic evidence; LLM backfills missing/invalid ones.
                                #
                                # v1.2.0 Track A: extend the merge trigger to non-vat_invoice categories.
                                # Under v1.1.x the merge only ran when is_valid_tax_invoice=True, but
                                # receipt/proforma/overseas_invoice/other legitimately have is_valid_tax_invoice=False;
                                # without this, overseas invoices would silently fail to populate invoice_no.
                                if extracted.buyer and extracted.buyer != "未知":
                                    parsed.buyer = extracted.buyer
                                if extracted.seller and extracted.seller != "未知":
                                    parsed.seller = extracted.seller
                                if extracted.item_summary and extracted.item_summary != "未知":
                                    parsed.item_summary = extracted.item_summary
                                if extracted.invoice_type and extracted.invoice_type != "未知":
                                    parsed.invoice_type = extracted.invoice_type
                                parsed.invoice_category = extracted.invoice_category.value

                                # invoice_no: parser wins if deterministic 8/20-digit match
                                if not parser_invoice_no_looks_valid and extracted.invoice_no:
                                    parsed.invoice_no = extracted.invoice_no
                                # date: parser wins if present
                                if parsed.invoice_date is None and extracted.invoice_date:
                                    parsed.invoice_date = extracted.invoice_date
                                # amount: parser wins unless it's missing/sentinel
                                if (parsed.amount is None or amount_is_sentinel) and extracted.amount:
                                    parsed.amount = extracted.amount
                                    amount_is_sentinel = parsed.amount < Decimal("0.10")

                                parsed.extraction_method = "llm"
                                parsed.confidence = max(extracted.confidence, parsed.confidence)

                        scam_text = " ".join(
                            s for s in (parsed.buyer, parsed.seller, parsed.item_summary) if s
                        )
                        scam_hit, scam_reason = is_scam_text(scam_text)
                        if scam_hit:
                            db.add(
                                _record_extraction_log(
                                    user_id=user_id,
                                    scan_log_id=log_id,
                                    email_uid=email_data.uid,
                                    email_subject=email_data.subject,
                                    attachment_filename=filename,
                                    outcome="not_vat_invoice",
                                    classification_tier=classification_tier,
                                    parse_method=parsed.extraction_method,
                                    parse_format=parsed.source_format,
                                    invoice_no=parsed.invoice_no,
                                    confidence=parsed.confidence,
                                    error_detail=f"scam signal: {scam_reason}",
                                )
                            )
                            await db.commit()
                            continue

                        final_type = parsed.invoice_type or ""
                        type_is_valid = (
                            final_type in VALID_INVOICE_TYPES
                            or any(vt in final_type for vt in VALID_INVOICE_TYPES if len(vt) > 3)
                        )
                        # LLM veto: only honour when parser evidence is weak. A strong parse
                        # (QR/XML/OFD struct, or regex-matched 8/20-digit invoice_no) must not
                        # be discarded just because the LLM guessed is_valid_tax_invoice=false.
                        llm_rejected = (
                            extracted is not None
                            and not extracted.is_valid_tax_invoice
                            and not strong_parse
                        )
                        heuristic_rejected = extracted is None and not parsed.is_vat_document and not type_is_valid

                        # v1.2.0 Track A: rejection rule gated by STRICT_VAT_ONLY.
                        # Default false: only reject invoice_category=vat_invoice
                        # rows with is_valid_tax_invoice=false. Non-VAT categories
                        # (receipt / proforma / overseas_invoice / other) save normally.
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
                            db.add(
                                _record_extraction_log(
                                    user_id=user_id,
                                    scan_log_id=log_id,
                                    email_uid=email_data.uid,
                                    email_subject=email_data.subject,
                                    attachment_filename=filename,
                                    outcome="not_vat_invoice",
                                    classification_tier=classification_tier,
                                    parse_method=parsed.extraction_method,
                                    parse_format=parsed.source_format,
                                    invoice_no=parsed.invoice_no,
                                    confidence=parsed.confidence,
                                    error_detail=f"type={final_type!r} category={final_category!r} llm_rejected={llm_rejected}",
                                )
                            )
                            await db.commit()
                            continue

                        amount_is_sentinel = parsed.amount is not None and parsed.amount < Decimal("0.10")
                        if parsed.confidence < 0.6 or not parsed.invoice_no or amount_is_sentinel:
                            db.add(
                                _record_extraction_log(
                                    user_id=user_id,
                                    scan_log_id=log_id,
                                    email_uid=email_data.uid,
                                    email_subject=email_data.subject,
                                    attachment_filename=filename,
                                    outcome="low_confidence",
                                    classification_tier=classification_tier,
                                    parse_method=parsed.extraction_method,
                                    parse_format=parsed.source_format,
                                    invoice_no=parsed.invoice_no,
                                    confidence=parsed.confidence,
                                    error_detail=(
                                        "amount sentinel"
                                        if amount_is_sentinel
                                        else None if parsed.invoice_no else "invoice_no missing"
                                    ),
                                )
                            )
                            continue

                        existing = await db.execute(
                            select(Invoice).where(
                                Invoice.user_id == user_id,
                                Invoice.invoice_no == parsed.invoice_no,
                            )
                        )
                        if existing.scalar_one_or_none():
                            db.add(
                                _record_extraction_log(
                                    user_id=user_id,
                                    scan_log_id=log_id,
                                    email_uid=email_data.uid,
                                    email_subject=email_data.subject,
                                    attachment_filename=filename,
                                    outcome="duplicate",
                                    classification_tier=classification_tier,
                                    parse_method=parsed.extraction_method,
                                    parse_format=parsed.source_format,
                                    invoice_no=parsed.invoice_no,
                                )
                            )
                            continue

                        ext = (
                            f'.{filename.rsplit(".", 1)[-1].lower()}' if "." in filename else ".pdf"
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
                            email_uid=email_data.uid,
                            email_account_id=account_id,
                            source_format=parsed.source_format,
                            extraction_method=parsed.extraction_method,
                            confidence=parsed.confidence,
                        )
                        db.add(invoice)
                        try:
                            await db.flush()
                        except IntegrityError:
                            await db.rollback()
                            db.add(
                                _record_extraction_log(
                                    user_id=user_id,
                                    scan_log_id=log_id,
                                    email_uid=email_data.uid,
                                    email_subject=email_data.subject,
                                    attachment_filename=filename,
                                    outcome="duplicate",
                                    classification_tier=classification_tier,
                                    parse_method=parsed.extraction_method,
                                    parse_format=parsed.source_format,
                                    invoice_no=parsed.invoice_no,
                                )
                            )
                            await db.commit()
                            continue

                        db.add(
                            _record_extraction_log(
                                user_id=user_id,
                                scan_log_id=log_id,
                                email_uid=email_data.uid,
                                email_subject=email_data.subject,
                                attachment_filename=filename,
                                outcome="saved",
                                classification_tier=classification_tier,
                                parse_method=parsed.extraction_method,
                                parse_format=parsed.source_format,
                                invoice_no=parsed.invoice_no,
                                confidence=parsed.confidence,
                            )
                        )

                        if settings.sqlite_vec_available:
                            try:
                                search_text = (
                                    f"{parsed.buyer} {parsed.seller} {parsed.item_summary or ''}"
                                )
                                embedding = await ai.embed_text(search_text, db)
                                await store_embedding(db, invoice.id, embedding)
                            except Exception as exc:
                                logger.warning(
                                    "Embedding failed for invoice %s: %s",
                                    parsed.invoice_no,
                                    exc,
                                )

                        result.invoices_added += 1
                        await db.commit()
                        await _send_invoice_webhook(db, settings, invoice)
                    except Exception as exc:
                        logger.error("Failed to process invoice payload %s: %s", filename, exc)
                        db.add(
                            _record_extraction_log(
                                user_id=user_id,
                                scan_log_id=log_id,
                                email_uid=email_data.uid,
                                email_subject=email_data.subject,
                                attachment_filename=filename,
                                outcome="error",
                                classification_tier=classification_tier,
                                error_detail=str(exc)[:500],
                            )
                        )
                        await db.commit()

                await db.commit()
            except Exception as exc:
                result.error = str(exc)[:500]
                await db.rollback()
                logger.error("Failed to process email %s: %s", email_data.subject[:60], exc)

            return result
    return _EmailResult()


async def scan_all_accounts(options: ScanOptions | None = None, account_id: int | None = None) -> None:
    """Iterate active email accounts and ingest new invoices."""
    settings = get_settings()
    ai = AIService(settings)
    file_mgr = FileManager(settings.STORAGE_PATH)

    async with sp._scan_lock:
        try:
            async for db in get_db():
                await db.execute(
                    text(
                        "UPDATE scan_logs SET finished_at = :ts, error_message = :msg"
                        " WHERE finished_at IS NULL AND error_message IS NULL"
                    ),
                    {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "msg": "Scan interrupted — orphan log cleaned up at next scan start",
                    },
                )
                await db.commit()
                query = select(EmailAccount).where(EmailAccount.is_active.is_(True))
                if account_id is not None:
                    query = query.where(EmailAccount.id == account_id)
                result = await db.execute(query)
                accounts = list(result.scalars().all())
                classifier = await _load_classifier(db)
                sp.reset_progress(total_accounts=len(accounts))

                for account_idx, account in enumerate(accounts):
                    account_id = account.id
                    account_name = account.name
                    account_user_id = account.user_id
                    await sp.update_progress(
                        current_account_idx=account_idx + 1,
                        current_account_name=account_name,
                        total_emails=0,
                        emails_processed=0,
                    )
                    log = ScanLog(
                        user_id=account_user_id,
                        email_account_id=account_id,
                        started_at=datetime.now(timezone.utc),
                        emails_scanned=0,
                        invoices_found=0,
                    )
                    db.add(log)
                    await db.commit()
                    await db.refresh(log)

                    try:
                        if ScraperFactory.is_scraper_type(account.type):
                            scanner = ScraperFactory.get_scraper(account.type)
                        else:
                            scanner = ScannerFactory.get_scanner(account.type)
                        scan_loop = asyncio.get_running_loop()

                        def _publish_scan_progress(update: dict[str, Any]) -> None:
                            try:
                                asyncio.run_coroutine_threadsafe(
                                    sp.update_progress(**update), scan_loop
                                )
                            except Exception:  # pragma: no cover
                                pass

                        scan_kwargs: dict[str, Any] = {
                            "last_uid": account.last_scan_uid,
                            "options": options,
                        }
                        try:
                            emails = await scanner.scan(
                                account,
                                progress_callback=_publish_scan_progress,
                                **scan_kwargs,
                            )
                        except TypeError:
                            emails = await scanner.scan(account, **scan_kwargs)
                        log.emails_scanned = len(emails)
                        await sp.update_progress(
                            total_emails=len(emails),
                        )

                        tasks = [
                            _process_single_email(
                                email_data=email,
                                classifier=classifier,
                                ai=ai,
                                file_mgr=file_mgr,
                                settings=settings,
                                log_id=log.id,
                                account_id=account.id,
                                user_id=account.user_id,
                                scanner=scanner,
                                account=account,
                            )
                            for email in emails
                        ]

                        last_uid = account.last_scan_uid
                        invoices_added = 0

                        for coro in asyncio.as_completed(tasks):
                            try:
                                email_result = await coro
                            except Exception:
                                await sp.inc_errors()
                                await sp.inc_emails_processed()
                                continue

                            invoices_added += email_result.invoices_added
                            if email_result.invoices_added > 0:
                                await sp.inc_invoices_found(email_result.invoices_added)
                            if email_result.error:
                                await sp.inc_errors()
                            if email_result.last_uid and (
                                not last_uid or _is_uid_newer(email_result.last_uid, last_uid)
                            ):
                                last_uid = email_result.last_uid
                            await sp.inc_emails_processed()

                        scanner_events = getattr(scanner, "_scan_events", None) or []
                        for event in scanner_events:
                            kind = str(event.get("kind") or "")
                            folder_id = str(event.get("folder_id") or "")
                            db.add(
                                _record_extraction_log(
                                    user_id=account.user_id,
                                    scan_log_id=log.id,
                                    email_uid=(
                                        event.get("email_uid")
                                        or f"scanner:{kind}:{folder_id}"
                                    ),
                                    email_subject=event.get("email_subject")
                                    or "(scanner diagnostic)",
                                    attachment_filename=event.get("attachment_filename"),
                                    outcome=kind,
                                    error_detail=event.get("error_detail"),
                                )
                            )

                        scan_state = getattr(scanner, "_last_scan_state", None)
                        if scan_state is not None:
                            if scan_state != account.last_scan_uid:
                                account.last_scan_uid = scan_state
                        elif last_uid and last_uid != account.last_scan_uid:
                            account.last_scan_uid = last_uid

                        updated_storage = getattr(scanner, "_updated_storage_state", None)
                        if updated_storage is not None and updated_storage != account.playwright_storage_state:
                            account.playwright_storage_state = updated_storage

                        log.invoices_found = invoices_added
                        log.finished_at = datetime.now(timezone.utc)
                        await db.commit()
                    except Exception as exc:
                        await db.rollback()
                        error_msg = str(exc)[:500]
                        logger.exception("Scan failed for account %s (%s)", account_name, account_id)
                        await sp.inc_errors()
                        try:
                            await db.execute(
                                text(
                                    "UPDATE scan_logs SET error_message = :msg, finished_at = :ts"
                                    " WHERE id = :id"
                                ),
                                {
                                    "msg": error_msg,
                                    "ts": datetime.now(timezone.utc).isoformat(),
                                    "id": log.id,
                                },
                            )
                            await db.commit()  # pragma: no cover
                        except Exception:  # pragma: no cover
                            pass
                        continue

            await sp.finish_progress()
        except Exception as exc:
            await sp.finish_progress(error=str(exc))
            raise


LLM_CACHE_CLEANUP_BATCH_SIZE = 5000
EXTRACTION_LOG_RETENTION_DAYS = 90
EXTRACTION_LOG_CLEANUP_BATCH_SIZE = 10000


async def cleanup_llm_cache() -> int:
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")
    async for db in get_db():
        try:
            result = await db.execute(
                text(
                    "DELETE FROM llm_cache WHERE id IN ("
                    "  SELECT id FROM llm_cache"
                    "  WHERE expires_at IS NOT NULL AND expires_at < :now"
                    "  LIMIT :batch"
                    ")"
                ),
                {"now": now, "batch": LLM_CACHE_CLEANUP_BATCH_SIZE},
            )
            await db.commit()
            deleted = result.rowcount or 0
            if deleted > 0:
                logger.info("LLM cache cleanup removed %d expired entries", deleted)
            return deleted
        except Exception:  # pragma: no cover
            await db.rollback()
            logger.exception("LLM cache cleanup failed")
            return 0
    return 0  # pragma: no cover


async def cleanup_extraction_logs() -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=EXTRACTION_LOG_RETENTION_DAYS)
    cutoff_str = cutoff.replace(tzinfo=None).isoformat(sep=" ")
    async for db in get_db():
        try:
            result = await db.execute(
                text(
                    "DELETE FROM extraction_logs WHERE id IN ("
                    "  SELECT id FROM extraction_logs"
                    "  WHERE created_at < :cutoff"
                    "  LIMIT :batch"
                    ")"
                ),
                {"cutoff": cutoff_str, "batch": EXTRACTION_LOG_CLEANUP_BATCH_SIZE},
            )
            await db.commit()
            deleted = result.rowcount or 0
            if deleted > 0:
                logger.info(
                    "Extraction log cleanup removed %d entries older than %d days",
                    deleted,
                    EXTRACTION_LOG_RETENTION_DAYS,
                )
            return deleted
        except Exception:  # pragma: no cover
            await db.rollback()
            logger.exception("Extraction log cleanup failed")
            return 0
    return 0  # pragma: no cover


def start_scheduler(settings: Settings) -> None:
    global _scheduler
    existing_scheduler = _scheduler
    if existing_scheduler is not None and existing_scheduler.running:
        return

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        scan_all_accounts,
        "interval",
        minutes=settings.SCAN_INTERVAL_MINUTES,
        id="email_scan",
        replace_existing=True,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        cleanup_llm_cache,
        "interval",
        hours=1,
        id="llm_cache_cleanup",
        replace_existing=True,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        cleanup_extraction_logs,
        "interval",
        hours=24,
        id="extraction_log_cleanup",
        replace_existing=True,
        misfire_grace_time=300,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info("Scheduler started. Scan interval: %d minutes", settings.SCAN_INTERVAL_MINUTES)


def get_scheduler() -> AsyncIOScheduler | None:
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return

    _scheduler.shutdown(wait=False)
    _scheduler = None
    logger.info("Scheduler stopped")
