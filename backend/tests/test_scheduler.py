from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

import app.tasks.scheduler as scheduler
from app.models import Invoice, ScanLog
from app.services.email_scanner import RawAttachment, RawEmail
from app.services.invoice_parser import ParsedInvoice


@pytest.mark.asyncio
async def test_scan_all_accounts_happy_path_with_embedding(
    db, settings, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    settings.sqlite_vec_available = True
    account = await create_email_account(last_scan_uid="1")
    email = RawEmail(
        uid="2",
        subject="Invoice",
        body_text="body",
        body_html="",
        from_addr="sender@test",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[RawAttachment(filename="invoice.pdf", payload=b"pdf", content_type="application/pdf")],
    )
    parsed = ParsedInvoice(
        invoice_no="INV-SCHED-1",
        buyer="Buyer",
        seller="Seller",
        amount=Decimal("10.00"),
        invoice_date=date(2024, 1, 1),
        invoice_type="电子普通发票",
        item_summary="办公用品",
        raw_text="raw",
        source_format="pdf",
        extraction_method="regex",
        confidence=0.9,
    )

    class FakeScanner:
        async def scan(self, account, last_uid=None):
            del account, last_uid
            return [email]

    async def override_get_db():
        yield db

    monkeypatch.setattr(scheduler, "get_db", override_get_db)
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "parse_invoice", lambda filename, payload: parsed)
    monkeypatch.setattr(scheduler.FileManager, "save_invoice", AsyncMock(return_value="saved.pdf"))
    monkeypatch.setattr(scheduler, "store_embedding", AsyncMock(return_value=None))

    await scheduler.scan_all_accounts()

    invoices = (await db.execute(select(Invoice))).scalars().all()
    logs = (await db.execute(select(ScanLog))).scalars().all()
    assert len(invoices) == 1
    assert invoices[0].invoice_no == "INV-SCHED-1"
    assert account.last_scan_uid == "2"
    assert logs[0].invoices_found == 1


@pytest.mark.asyncio
async def test_scan_all_accounts_handles_duplicates_llm_enrichment_and_errors(
    db, create_email_account, create_invoice, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    account = await create_email_account(last_scan_uid=None)
    await create_invoice(invoice_no="INV-DUPLICATE", email_account=account)
    email = RawEmail(
        uid="uid-9",
        subject="Invoice",
        body_text="body",
        body_html="",
        from_addr="sender@test",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[
            RawAttachment(filename="duplicate.pdf", payload=b"1", content_type="application/pdf"),
            RawAttachment(filename="enrich.pdf", payload=b"2", content_type="application/pdf"),
            RawAttachment(filename="bad.pdf", payload=b"3", content_type="application/pdf"),
        ],
    )
    parsed_results = iter(
        [
            ParsedInvoice(invoice_no="INV-DUPLICATE", raw_text="duplicate", confidence=0.9),
            ParsedInvoice(invoice_no=None, raw_text="needs llm", confidence=0.1),
            RuntimeError("broken attachment"),
        ]
    )

    class FakeScanner:
        async def scan(self, account, last_uid=None):
            del account, last_uid
            return [email]

    async def override_get_db():
        yield db

    def parse_invoice(filename, payload):
        del filename, payload
        item = next(parsed_results)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(scheduler, "get_db", override_get_db)
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "parse_invoice", parse_invoice)
    monkeypatch.setattr(scheduler.FileManager, "save_invoice", AsyncMock(return_value="saved-llm.pdf"))

    await scheduler.scan_all_accounts()

    invoices = (await db.execute(select(Invoice).order_by(Invoice.id))).scalars().all()
    logs = (await db.execute(select(ScanLog))).scalars().all()
    assert len(invoices) == 2
    assert invoices[-1].invoice_no == mock_ai_service.extract_invoice_fields.return_value.invoice_no
    assert invoices[-1].extraction_method == "llm"
    assert logs[0].invoices_found == 1


@pytest.mark.asyncio
async def test_scan_all_accounts_rollback_on_scanner_error(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    _account = await create_email_account()
    monkeypatch.setattr(db, "rollback", AsyncMock(return_value=None))

    class BrokenScanner:
        async def scan(self, account, last_uid=None):
            del account, last_uid
            raise RuntimeError("scanner failed")

    async def override_get_db():
        yield db

    monkeypatch.setattr(scheduler, "get_db", override_get_db)
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: BrokenScanner())

    await scheduler.scan_all_accounts()

    logs = (await db.execute(select(ScanLog))).scalars().all()
    assert logs[0].error_message == "scanner failed"


def test_start_and_stop_scheduler(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    scheduler._scheduler = None
    captured = {}

    class FakeScheduler:
        def __init__(self, timezone):
            self.timezone = timezone
            self.running = False
            self.started = False

        def add_job(self, func, trigger, minutes, id, replace_existing, misfire_grace_time):
            captured.update(
                {
                    "func": func,
                    "trigger": trigger,
                    "minutes": minutes,
                    "id": id,
                    "replace_existing": replace_existing,
                    "misfire_grace_time": misfire_grace_time,
                }
            )

        def start(self):
            self.running = True
            self.started = True

        def shutdown(self, wait=False):
            self.running = False
            captured["shutdown_wait"] = wait

    monkeypatch.setattr(scheduler, "AsyncIOScheduler", FakeScheduler)
    scheduler.start_scheduler(settings)
    assert captured["minutes"] == settings.SCAN_INTERVAL_MINUTES
    existing = scheduler._scheduler
    scheduler.start_scheduler(settings)
    assert scheduler._scheduler is existing
    scheduler.stop_scheduler()
    assert captured["shutdown_wait"] is False
    assert scheduler._scheduler is None
    scheduler.stop_scheduler()


@pytest.mark.asyncio
async def test_scan_all_accounts_skips_non_invoice_missing_number_and_embedding_failure(
    db, settings, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    settings.sqlite_vec_available = True
    _account = await create_email_account(last_scan_uid=None)
    emails = [
        RawEmail(uid="1", subject="no", body_text="body", body_html="", from_addr="a@test", received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc), attachments=[RawAttachment(filename="a.pdf", payload=b"1", content_type="application/pdf")]),
        RawEmail(uid="", subject="yes", body_text="body", body_html="", from_addr="a@test", received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc), attachments=[RawAttachment(filename="b.pdf", payload=b"2", content_type="application/pdf")]),
    ]
    classify = AsyncMock(side_effect=[False, True])
    mock_ai_service.classify_email = classify
    parsed_results = iter([ParsedInvoice(invoice_no=None, raw_text="raw", confidence=0.9)])

    class FakeScanner:
        async def scan(self, account, last_uid=None):
            del account, last_uid
            return emails

    async def override_get_db():
        yield db

    monkeypatch.setattr(scheduler, "get_db", override_get_db)
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "parse_invoice", lambda filename, payload: next(parsed_results))
    monkeypatch.setattr(scheduler.FileManager, "save_invoice", AsyncMock(return_value="saved.pdf"))
    monkeypatch.setattr(scheduler, "store_embedding", AsyncMock(side_effect=RuntimeError("embed fail")))

    await scheduler.scan_all_accounts()

    logs = (await db.execute(select(ScanLog))).scalars().all()
    assert logs[0].emails_scanned == 2
    assert logs[0].invoices_found == 0


@pytest.mark.asyncio
async def test_scan_all_accounts_embedding_failure_logs_warning(
    db, settings, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    settings.sqlite_vec_available = True
    await create_email_account(last_scan_uid=None)
    email = RawEmail(
        uid="2",
        subject="Invoice",
        body_text="body",
        body_html="",
        from_addr="sender@test",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[RawAttachment(filename="invoice.pdf", payload=b"pdf", content_type="application/pdf")],
    )
    parsed = ParsedInvoice(
        invoice_no="INV-EMBED-FAIL",
        buyer="Buyer",
        seller="Seller",
        amount=Decimal("10.00"),
        invoice_date=date(2024, 1, 1),
        raw_text="raw",
        source_format="pdf",
        extraction_method="regex",
        confidence=0.9,
    )
    warnings: list[str] = []

    class FakeScanner:
        async def scan(self, account, last_uid=None):
            del account, last_uid
            return [email]

    async def override_get_db():
        yield db

    monkeypatch.setattr(scheduler, "get_db", override_get_db)
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "parse_invoice", lambda filename, payload: parsed)
    monkeypatch.setattr(scheduler.FileManager, "save_invoice", AsyncMock(return_value="saved.pdf"))
    monkeypatch.setattr(scheduler, "store_embedding", AsyncMock(side_effect=RuntimeError("embed fail")))
    monkeypatch.setattr(scheduler.logger, "warning", lambda message, *args: warnings.append(message % args))

    await scheduler.scan_all_accounts()

    assert any("Embedding failed for invoice INV-EMBED-FAIL" in warning for warning in warnings)
