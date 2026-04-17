# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false, reportOptionalMemberAccess=false

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
import httpx
from sqlalchemy import select, text

from app.config import Settings, get_settings
from app.database import get_db
from app.models import AppSettings, EmailAccount, ExtractionLog, Invoice, ScanLog, WebhookLog
from app.services.ai_service import AIService, _resolve_safelink
from app.services.email_classifier import EmailClassifier, _parse_extra_keywords, _parse_trusted_senders
from app.services.email_scanner import ScannerFactory
from app.services.file_manager import FileManager
from app.services.invoice_parser import parse as parse_invoice
from app.services.search_service import store_embedding
from app.services import scan_progress as sp

logger = logging.getLogger(__name__)

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
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()
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
    scan_log_id: int,
    email_uid: str | None,
    email_subject: str,
    attachment_filename: str | None,
    outcome: str,
    classification_tier: int | None = None,
    invoice_no: str | None = None,
    confidence: float | None = None,
    error_detail: str | None = None,
) -> ExtractionLog:
    return ExtractionLog(
        scan_log_id=scan_log_id,
        email_uid=email_uid,
        email_subject=email_subject,
        attachment_filename=attachment_filename,
        outcome=outcome,
        classification_tier=classification_tier,
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
            event="invoice.created",
            invoice_no=invoice.invoice_no,
            url=settings.WEBHOOK_URL,
            status_code=status_code,
            success=success,
            error_detail=error_detail,
        )
    )
    await db.commit()


async def scan_all_accounts() -> None:
    """Iterate active email accounts and ingest new invoices."""
    settings = get_settings()
    ai = AIService(settings)
    file_mgr = FileManager(settings.STORAGE_PATH)

    async with sp._scan_lock:
        try:
            async for db in get_db():
                result = await db.execute(select(EmailAccount).where(EmailAccount.is_active.is_(True)))
                accounts = list(result.scalars().all())
                classifier = await _load_classifier(db)
                sp.reset_progress(total_accounts=len(accounts))

                for account_idx, account in enumerate(accounts):
                    account_id = account.id
                    account_name = account.name
                    sp.update_progress(
                        current_account_idx=account_idx,
                        current_account_name=account_name,
                        total_emails=0,
                        current_email_idx=0,
                        current_email_subject="",
                        total_attachments=0,
                        current_attachment_idx=0,
                        current_attachment_name="",
                    )
                    log = ScanLog(
                        email_account_id=account_id,
                        started_at=datetime.now(timezone.utc),
                        emails_scanned=0,
                        invoices_found=0,
                    )
                    db.add(log)
                    await db.commit()
                    await db.refresh(log)

                    try:
                        scanner = ScannerFactory.get_scanner(account.type)
                        emails = await scanner.scan(account, last_uid=account.last_scan_uid)
                        log.emails_scanned = len(emails)
                        sp.update_progress(
                            total_emails=len(emails),
                            current_email_idx=0,
                            current_email_subject="",
                            total_attachments=0,
                            current_attachment_idx=0,
                            current_attachment_name="",
                        )

                        last_uid = account.last_scan_uid
                        invoices_added = 0

                        for email_idx, email in enumerate(emails):
                            sp.update_progress(
                                current_email_idx=email_idx,
                                current_email_subject=email.subject[:80],
                                total_attachments=0,
                                current_attachment_idx=0,
                                current_attachment_name="",
                            )
                            t1 = classifier.classify_tier1(email)
                            if t1 is not None:
                                is_invoice = t1.is_invoice
                                classification_tier = 1
                                # Tier 1 positive with body links: still ask
                                # LLM for best download URL + format hints.
                                if is_invoice and email.body_links:
                                    analysis = await ai.analyze_email(
                                        db,
                                        subject=email.subject,
                                        from_addr=email.from_addr,
                                        body=email.body_text,
                                        body_links=email.body_links,
                                    )
                                else:
                                    analysis = None
                            else:
                                analysis = await ai.analyze_email(
                                    db,
                                    subject=email.subject,
                                    from_addr=email.from_addr,
                                    body=email.body_text,
                                    body_links=email.body_links,
                                )
                                is_invoice = analysis.is_invoice_related
                                classification_tier = 3
                                sp.update_progress(last_classification_tier=3)

                            if not is_invoice:
                                db.add(
                                    _record_extraction_log(
                                        scan_log_id=log.id,
                                        email_uid=email.uid,
                                        email_subject=email.subject,
                                        attachment_filename=None,
                                        outcome="not_invoice",
                                        classification_tier=classification_tier,
                                    )
                                )
                                sp.update_progress(
                                    last_classification_tier=classification_tier,
                                    emails_processed=sp.get_progress().emails_processed + 1,
                                )
                                continue

                            sp.update_progress(last_classification_tier=classification_tier)

                            raw_items: list[tuple[str, bytes]] = [
                                (att.filename, att.payload) for att in email.attachments
                            ]

                            if analysis is not None and analysis.should_download:
                                url = analysis.best_download_url
                                assert url is not None
                                if analysis.url_is_safelink:
                                    url = await _resolve_safelink(url)
                                sp.update_progress(
                                    current_attachment_url=(url or "")[:120],
                                    current_download_outcome="downloading",
                                )
                                downloaded = await _download_linked_invoice(url)
                                if downloaded is not None:
                                    raw_items.append(downloaded)
                                    sp.update_progress(current_download_outcome="saved")
                                else:
                                    sp.update_progress(current_download_outcome="failed")

                            raw_items = _prioritize_raw_items_with_hints(
                                raw_items,
                                analysis.extraction_hints.likely_formats if analysis is not None else None,
                            )

                            sp.update_progress(
                                total_attachments=len(raw_items),
                                current_attachment_idx=0,
                                current_attachment_name="",
                            )

                            for attachment_idx, (filename, payload) in enumerate(raw_items):
                                sp.update_progress(
                                    current_attachment_idx=attachment_idx,
                                    current_attachment_name=filename,
                                )
                                if await _was_attachment_seen(db, email.uid, filename):
                                    db.add(
                                        _record_extraction_log(
                                            scan_log_id=log.id,
                                            email_uid=email.uid,
                                            email_subject=email.subject,
                                            attachment_filename=filename,
                                            outcome="skipped_seen",
                                            classification_tier=classification_tier,
                                        )
                                    )
                                    continue

                                try:
                                    parsed = parse_invoice(filename, payload)
                                    sp.update_progress(
                                        current_parse_method=parsed.extraction_method,
                                        current_parse_format=parsed.source_format,
                                        current_attachment_url="",
                                        current_download_outcome="",
                                    )

                                    if parsed.confidence < 0.5 and parsed.raw_text:
                                        extracted = await ai.extract_invoice_fields(db, parsed.raw_text)
                                        parsed.buyer = parsed.buyer or extracted.buyer
                                        parsed.seller = parsed.seller or extracted.seller
                                        parsed.invoice_no = parsed.invoice_no or extracted.invoice_no
                                        parsed.invoice_date = parsed.invoice_date or extracted.invoice_date
                                        parsed.amount = parsed.amount or extracted.amount
                                        parsed.item_summary = parsed.item_summary or extracted.item_summary
                                        parsed.invoice_type = parsed.invoice_type or extracted.invoice_type
                                        parsed.extraction_method = "llm"
                                        parsed.confidence = extracted.confidence

                                    if parsed.confidence < 0.5 or not parsed.invoice_no:
                                        db.add(
                                            _record_extraction_log(
                                                scan_log_id=log.id,
                                                email_uid=email.uid,
                                                email_subject=email.subject,
                                                attachment_filename=filename,
                                                outcome="low_confidence",
                                                classification_tier=classification_tier,
                                                invoice_no=parsed.invoice_no,
                                                confidence=parsed.confidence,
                                                error_detail=None if parsed.invoice_no else "invoice_no missing",
                                            )
                                        )
                                        continue

                                    existing = await db.execute(
                                        select(Invoice).where(Invoice.invoice_no == parsed.invoice_no)
                                    )
                                    if existing.scalar_one_or_none() is not None:
                                        db.add(
                                            _record_extraction_log(
                                                scan_log_id=log.id,
                                                email_uid=email.uid,
                                                email_subject=email.subject,
                                                attachment_filename=filename,
                                                outcome="duplicate",
                                                classification_tier=classification_tier,
                                                invoice_no=parsed.invoice_no,
                                                confidence=parsed.confidence,
                                            )
                                        )
                                        continue

                                    ext = (
                                        f'.{filename.rsplit(".", 1)[-1].lower()}'
                                        if "." in filename
                                        else ".pdf"
                                    )
                                    file_path = await file_mgr.save_invoice(
                                        payload,
                                        parsed.buyer,
                                        parsed.seller,
                                        parsed.invoice_no,
                                        parsed.invoice_date,
                                        parsed.amount,
                                        ext,
                                    )

                                    invoice = Invoice(
                                        invoice_no=parsed.invoice_no,
                                        buyer=parsed.buyer or "未知",
                                        seller=parsed.seller or "未知",
                                        amount=parsed.amount or 0,
                                        invoice_date=parsed.invoice_date
                                        or datetime.now(timezone.utc).date(),
                                        invoice_type=parsed.invoice_type or "未知",
                                        item_summary=parsed.item_summary,
                                        file_path=file_path,
                                        raw_text=parsed.raw_text[:10000],
                                        email_uid=email.uid,
                                        email_account_id=account.id,
                                        source_format=parsed.source_format,
                                        extraction_method=parsed.extraction_method,
                                        confidence=parsed.confidence,
                                    )
                                    db.add(invoice)
                                    await db.flush()
                                    sp.update_progress(
                                        invoices_found=sp.get_progress().invoices_found + 1,
                                        current_download_outcome="saved",
                                    )
                                    db.add(
                                        _record_extraction_log(
                                            scan_log_id=log.id,
                                            email_uid=email.uid,
                                            email_subject=email.subject,
                                            attachment_filename=filename,
                                            outcome="saved",
                                            classification_tier=classification_tier,
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

                                    invoices_added += 1
                                    await db.commit()
                                    await _send_invoice_webhook(db, settings, invoice)
                                except Exception as exc:
                                    logger.error("Failed to process invoice payload %s: %s", filename, exc)
                                    db.add(
                                        _record_extraction_log(
                                            scan_log_id=log.id,
                                            email_uid=email.uid,
                                            email_subject=email.subject,
                                            attachment_filename=filename,
                                            outcome="parse_error",
                                            classification_tier=classification_tier,
                                            error_detail=str(exc),
                                        )
                                    )
                                    sp.update_progress(errors=sp.get_progress().errors + 1)

                            if email.uid and (not last_uid or email.uid > last_uid):
                                last_uid = email.uid

                            sp.update_progress(emails_processed=sp.get_progress().emails_processed + 1)

                        if last_uid and last_uid != account.last_scan_uid:
                            account.last_scan_uid = last_uid

                        log.invoices_found = invoices_added
                        log.finished_at = datetime.now(timezone.utc)
                        await db.commit()
                    except Exception as exc:
                        await db.rollback()
                        error_msg = str(exc)[:500]
                        logger.exception("Scan failed for account %s (%s)", account_name, account_id)
                        sp.update_progress(errors=sp.get_progress().errors + 1)
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

            sp.finish_progress()
        except Exception as exc:
            sp.finish_progress(error=str(exc))
            raise


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
