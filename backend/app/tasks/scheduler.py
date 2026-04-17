# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false, reportOptionalMemberAccess=false

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
import httpx
from sqlalchemy import select

from app.config import Settings, get_settings
from app.database import get_db
from app.models import EmailAccount, Invoice, ScanLog
from app.services.ai_service import AIService
from app.services.email_scanner import ScannerFactory
from app.services.file_manager import FileManager
from app.services.invoice_parser import parse as parse_invoice
from app.services.search_service import store_embedding

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


async def scan_all_accounts() -> None:
    """Iterate active email accounts and ingest new invoices."""
    settings = get_settings()
    ai = AIService(settings)
    file_mgr = FileManager(settings.STORAGE_PATH)

    async for db in get_db():
        result = await db.execute(select(EmailAccount).where(EmailAccount.is_active.is_(True)))
        accounts = list(result.scalars().all())

        for account in accounts:
            account_id = account.id
            account_name = account.name
            log = ScanLog(
                email_account_id=account_id,
                started_at=datetime.now(timezone.utc),
                emails_scanned=0,
                invoices_found=0,
            )

            try:
                scanner = ScannerFactory.get_scanner(account.type)
                emails = await scanner.scan(account, last_uid=account.last_scan_uid)
                log.emails_scanned = len(emails)

                last_uid = account.last_scan_uid
                invoices_added = 0

                for email in emails:
                    is_invoice = await ai.classify_email(db, email.subject, email.body_text)
                    if not is_invoice:
                        continue

                    raw_items: list[tuple[str, bytes]] = [(att.filename, att.payload) for att in email.attachments]
                    seen_links: set[str] = set()
                    for link in email.body_links:
                        if link in seen_links:
                            continue
                        seen_links.add(link)
                        downloaded = await _download_linked_invoice(link)
                        if downloaded is not None:
                            raw_items.append(downloaded)

                    for filename, payload in raw_items:
                        try:
                            parsed = parse_invoice(filename, payload)

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

                            if not parsed.invoice_no:
                                continue

                            existing = await db.execute(
                                select(Invoice).where(Invoice.invoice_no == parsed.invoice_no)
                            )
                            if existing.scalar_one_or_none() is not None:
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
                            )

                            invoice = Invoice(
                                invoice_no=parsed.invoice_no,
                                buyer=parsed.buyer or "未知",
                                seller=parsed.seller or "未知",
                                amount=parsed.amount or 0,
                                invoice_date=parsed.invoice_date or datetime.now(timezone.utc).date(),
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

                            if settings.sqlite_vec_available:
                                try:
                                    search_text = f"{parsed.buyer} {parsed.seller} {parsed.item_summary or ''}"
                                    embedding = await ai.embed_text(search_text)
                                    await store_embedding(db, invoice.id, embedding)
                                except Exception as exc:
                                    logger.warning(
                                        "Embedding failed for invoice %s: %s", parsed.invoice_no, exc
                                    )

                            invoices_added += 1
                        except Exception as exc:
                            logger.error("Failed to process invoice payload %s: %s", filename, exc)

                    if email.uid and (not last_uid or email.uid > last_uid):
                        last_uid = email.uid

                if last_uid and last_uid != account.last_scan_uid:
                    account.last_scan_uid = last_uid

                log.invoices_found = invoices_added
                await db.commit()
            except Exception as exc:
                await db.rollback()
                log.error_message = str(exc)[:500]
                logger.exception("Scan failed for account %s (%s)", account_name, account_id)
            finally:
                log.finished_at = datetime.now(timezone.utc)
                db.add(log)
                await db.commit()


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


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return

    _scheduler.shutdown(wait=False)
    _scheduler = None
    logger.info("Scheduler stopped")
