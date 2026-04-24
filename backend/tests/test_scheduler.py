from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.tasks.scheduler as scheduler
from app.models import EmailAccount, ExtractionLog, Invoice, LLMCache, ScanLog, WebhookLog
from app.schemas.invoice import EmailAnalysis, InvoiceFormat, InvoicePlatform, UrlKind
from app.services import scan_progress as sp
from app.services.email_scanner import RawAttachment, RawEmail
from app.services.invoice_parser import ParsedInvoice


def make_get_db_override(db: AsyncSession):
    session_factory = async_sessionmaker(bind=db.bind, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    return override_get_db


def make_analysis(**overrides) -> EmailAnalysis:
    defaults = {
        "is_invoice_related": True,
        "invoice_confidence": 0.9,
        "best_download_url": None,
        "url_confidence": 0.0,
        "url_is_safelink": False,
        "url_kind": UrlKind.NONE,
        "skip_reason": None,
    }
    defaults.update(overrides)
    return EmailAnalysis(**defaults)


@pytest.mark.asyncio
async def test_scan_all_accounts_happy_path_with_embedding(
    db, settings, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    settings.sqlite_vec_available = True
    account = await create_email_account(last_scan_uid="1")
    email = RawEmail(
        uid="2",
        subject="Document ready",
        body_text="body",
        body_html="",
        from_addr="sender@test",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[RawAttachment(filename="doc.pdf", payload=b"pdf", content_type="application/pdf")],
    )
    parsed = ParsedInvoice(
        invoice_no="INV-SCHED-1",
        buyer="Buyer",
        seller="Seller",
        amount=Decimal("10.00"),
        invoice_date=date(2024, 1, 1),
        invoice_type="增值税电子普通发票",
        item_summary="办公用品",
        raw_text="增值税电子普通发票 价税合计 税额 发票号码",
        source_format="pdf",
        extraction_method="regex",
        confidence=0.9,
    )

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return [email]

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "parse_invoice", lambda filename, payload: parsed)
    monkeypatch.setattr(scheduler.FileManager, "save_invoice", AsyncMock(return_value="saved.pdf"))
    monkeypatch.setattr(scheduler, "store_embedding", AsyncMock(return_value=None))

    await scheduler.scan_all_accounts()
    await db.refresh(account)

    invoices = (await db.execute(select(Invoice))).scalars().all()
    logs = (await db.execute(select(ScanLog))).scalars().all()
    extraction_logs = (await db.execute(select(ExtractionLog))).scalars().all()
    assert len(invoices) == 1
    assert invoices[0].invoice_no == "INV-SCHED-1"
    assert account.last_scan_uid == "2"
    assert logs[0].invoices_found == 1
    assert extraction_logs[0].outcome == "saved"
    assert extraction_logs[0].classification_tier == 1


def test_record_extraction_log_truncates_long_error_detail() -> None:
    log = scheduler._record_extraction_log(
        user_id=1,
        scan_log_id=1,
        email_uid="uid",
        email_subject="subject",
        attachment_filename="file.pdf",
        outcome="error",
        error_detail="x" * 5000,
    )

    assert log.error_detail == "x" * 2000


@pytest.mark.asyncio
async def test_scan_all_accounts_tier1_attachment_hit_avoids_llm_call(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    await create_email_account(last_scan_uid=None)
    email = RawEmail(
        uid="trusted-1",
        subject="Status",
        body_text="normal body",
        body_html="",
        from_addr="trusted@example.com",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[RawAttachment(filename="doc.pdf", payload=b"pdf", content_type="application/pdf")],
    )
    parsed = ParsedInvoice(invoice_no="INV-TIER1", raw_text="增值税电子普通发票 价税合计 税额 发票号码", confidence=0.9, invoice_type="增值税电子普通发票")

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return [email]

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "parse_invoice", lambda filename, payload: parsed)
    monkeypatch.setattr(scheduler.FileManager, "save_invoice", AsyncMock(return_value="saved.pdf"))

    await scheduler.scan_all_accounts()

    extraction_logs = (await db.execute(select(ExtractionLog))).scalars().all()
    assert extraction_logs[0].classification_tier == 1
    mock_ai_service.analyze_email.assert_not_awaited()
    mock_ai_service.classify_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_scan_all_accounts_tier3_uses_raw_body_and_from_for_llm(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    await create_email_account(last_scan_uid=None)
    email = RawEmail(
        uid="tier3-1",
        subject="Portal update",
        body_text="Please review the latest portal notice for your account." * 30,
        body_html="",
        from_addr="sender@test",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[],
        body_links=["https://example.com/account"],
    )

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return [email]

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    mock_ai_service.analyze_email.return_value = make_analysis(
        is_invoice_related=False,
        invoice_confidence=0.4,
        skip_reason="非发票",
    )

    await scheduler.scan_all_accounts()

    extraction_logs = (await db.execute(select(ExtractionLog))).scalars().all()
    assert extraction_logs[0].outcome == "not_invoice"
    assert extraction_logs[0].classification_tier == 3
    kwargs = mock_ai_service.analyze_email.await_args.kwargs
    assert kwargs["subject"] == "Portal update"
    assert kwargs["from_addr"] == "sender@test"
    assert kwargs["body"] == email.body_text
    assert kwargs["body_links"] == ["https://example.com/account"]


@pytest.mark.asyncio
async def test_scan_all_accounts_handles_duplicates_llm_enrichment_and_errors(
    db, create_email_account, create_invoice, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    account = await create_email_account(last_scan_uid=None)
    await create_invoice(invoice_no="INV-DUPLICATE", email_account=account)
    email = RawEmail(
        uid="uid-9",
        subject="Portal documents",
        body_text="body",
        body_html="",
        from_addr="sender@test",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[
            RawAttachment(filename="duplicate-doc.pdf", payload=b"1", content_type="application/pdf"),
            RawAttachment(filename="enrich-doc.pdf", payload=b"2", content_type="application/pdf"),
            RawAttachment(filename="bad-doc.pdf", payload=b"3", content_type="application/pdf"),
        ],
    )
    parsed_results = iter(
        [
            ParsedInvoice(
                invoice_no="INV-DUPLICATE",
                raw_text="增值税电子普通发票 价税合计 税额 发票号码",
                confidence=0.9,
                invoice_type="增值税电子普通发票",
                buyer="Buyer",
                seller="Seller",
                amount=Decimal("10.00"),
                invoice_date=date(2024, 1, 1),
                item_summary="服务费",
                extraction_method="qr",
            ),
            ParsedInvoice(invoice_no=None, raw_text="needs llm", confidence=0.1, is_vat_document=True),
            RuntimeError("broken attachment"),
        ]
    )

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return [email]

    def parse_invoice(filename, payload):
        del filename, payload
        item = next(parsed_results)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "parse_invoice", parse_invoice)
    monkeypatch.setattr(scheduler.FileManager, "save_invoice", AsyncMock(return_value="saved-llm.pdf"))

    await scheduler.scan_all_accounts()

    invoices = (await db.execute(select(Invoice).order_by(Invoice.id))).scalars().all()
    logs = (await db.execute(select(ScanLog))).scalars().all()
    extraction_logs = (await db.execute(select(ExtractionLog).order_by(ExtractionLog.id))).scalars().all()
    assert len(invoices) == 2
    assert invoices[-1].invoice_no == mock_ai_service.extract_invoice_fields.return_value.invoice_no
    assert invoices[-1].extraction_method == "llm"
    assert logs[0].invoices_found == 1
    assert [item.outcome for item in extraction_logs] == ["duplicate", "saved", "error"]


@pytest.mark.asyncio
async def test_scan_all_accounts_rollback_on_scanner_error(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    _account = await create_email_account()
    monkeypatch.setattr(db, "rollback", AsyncMock(return_value=None))

    class BrokenScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            raise RuntimeError("scanner failed")

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: BrokenScanner())

    await scheduler.scan_all_accounts()

    logs = (await db.execute(select(ScanLog))).scalars().all()
    assert len(logs) == 1


@pytest.mark.asyncio
async def test_scan_all_accounts_marks_progress_error_and_reraises_on_outer_failure(
    monkeypatch: pytest.MonkeyPatch,
    settings,
) -> None:
    del settings

    class BrokenDBIterator:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise RuntimeError("db failed")

    def broken_get_db():
        return BrokenDBIterator()

    monkeypatch.setattr(scheduler, "get_db", broken_get_db)

    with pytest.raises(RuntimeError, match="db failed"):
        await scheduler.scan_all_accounts()

    assert sp.get_progress().phase is sp.ScanPhase.ERROR


def test_start_and_stop_scheduler(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    scheduler._scheduler = None
    jobs_added: list[dict] = []

    class FakeScheduler:
        def __init__(self, timezone):
            self.timezone = timezone
            self.running = False
            self.started = False

        def add_job(self, func, trigger, *, id, replace_existing, misfire_grace_time, **interval):
            jobs_added.append(
                {
                    "func": func,
                    "trigger": trigger,
                    "id": id,
                    "replace_existing": replace_existing,
                    "misfire_grace_time": misfire_grace_time,
                    **interval,
                }
            )

        def start(self):
            self.running = True
            self.started = True

        def shutdown(self, wait=False):
            self.running = False
            jobs_added.append({"shutdown_wait": wait})

    monkeypatch.setattr(scheduler, "AsyncIOScheduler", FakeScheduler)
    scheduler.start_scheduler(settings)

    job_by_id = {j["id"]: j for j in jobs_added if "id" in j}
    assert job_by_id["email_scan"]["minutes"] == settings.SCAN_INTERVAL_MINUTES
    assert job_by_id["llm_cache_cleanup"]["hours"] == 1
    assert job_by_id["extraction_log_cleanup"]["hours"] == 24
    assert job_by_id["email_scan"]["func"] is scheduler.scan_all_accounts
    assert job_by_id["llm_cache_cleanup"]["func"] is scheduler.cleanup_llm_cache
    assert job_by_id["extraction_log_cleanup"]["func"] is scheduler.cleanup_extraction_logs

    existing = scheduler._scheduler
    assert scheduler.get_scheduler() is existing
    scheduler.start_scheduler(settings)
    assert scheduler._scheduler is existing
    scheduler.stop_scheduler()
    assert any(j.get("shutdown_wait") is False for j in jobs_added)
    assert scheduler._scheduler is None
    assert scheduler.get_scheduler() is None
    scheduler.stop_scheduler()


@pytest.mark.parametrize(
    ("url", "content_type", "expected"),
    [
        ("https://example.com/invoice.xml?token=1", None, "download.xml"),
        ("https://example.com/path/file", "application/pdf", "download.pdf"),
        ("https://example.com/path/file", "text/xml; charset=utf-8", "download.xml"),
        ("https://example.com/path/file", "application/zip", "download.ofd"),
        ("https://example.com/path/file", "text/plain", "download.pdf"),
        ("https://example.com/path/file", None, "download.pdf"),
    ],
)
def test_guess_filename_from_link(url: str, content_type: str | None, expected: str) -> None:
    assert scheduler._guess_filename_from_link(url, content_type) == expected


def test_prioritize_raw_items_prefers_pdf_then_ofd_then_xml() -> None:
    items = [
        ("a.xml", b"1"),
        ("b.bin", b"2"),
        ("c.ofd", b"3"),
        ("d.pdf", b"4"),
    ]

    assert [name for name, _ in scheduler._prioritize_raw_items(items)] == ["d.pdf", "c.ofd", "a.xml", "b.bin"]


def test_prioritize_raw_items_with_hints_respects_pdf_first_confirmation() -> None:
    items = [("invoice.xml", b"1"), ("invoice.pdf", b"2")]

    prioritized = scheduler._prioritize_raw_items_with_hints(items, [InvoiceFormat.PDF, InvoiceFormat.XML])

    assert [name for name, _ in prioritized] == ["invoice.pdf", "invoice.xml"]


def test_prioritize_raw_items_with_hints_ofd_fallback() -> None:
    items = [("doc.ofd", b"1"), ("doc.pdf", b"2"), ("doc.xml", b"3")]

    prioritized = scheduler._prioritize_raw_items_with_hints(items, [InvoiceFormat.PDF])

    names = [name for name, _ in prioritized]
    assert names[0] == "doc.pdf"
    assert names.index("doc.ofd") < names.index("doc.xml")


@pytest.mark.asyncio
async def test_download_linked_invoice_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        headers = {"content-type": "application/xml"}
        content = b"<invoice />"

        def raise_for_status(self) -> None:
            pass

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url: str) -> FakeResponse:
            assert url == "https://example.com/invoice"
            return FakeResponse()

    monkeypatch.setattr(scheduler.httpx, "AsyncClient", lambda **kwargs: FakeClient())

    assert await scheduler._download_linked_invoice("https://example.com/invoice") == (
        "download.xml",
        b"<invoice />",
    )


@pytest.mark.asyncio
async def test_download_linked_invoice_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings: list[str] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url: str):
            raise scheduler.httpx.HTTPError(f"boom: {url}")

    monkeypatch.setattr(scheduler.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    monkeypatch.setattr(scheduler.logger, "warning", lambda message, *args: warnings.append(message % args))

    assert await scheduler._download_linked_invoice("https://example.com/invoice") is None
    assert any("Failed to download invoice link https://example.com/invoice" in warning for warning in warnings)


def test_is_blocked_download_url_recognizes_trackers_and_assets() -> None:
    assert scheduler._is_blocked_download_url("https://linktrace.triggerdelivery.com/xyz") is True
    assert scheduler._is_blocked_download_url("https://mail.example.com/unsubscribe?u=1") is True
    assert scheduler._is_blocked_download_url("https://example.com/track/open?id=42") is True
    assert scheduler._is_blocked_download_url("https://example.com/pixel.gif") is True
    assert scheduler._is_blocked_download_url("https://example.com/badge.png") is True
    assert scheduler._is_blocked_download_url("https://click.mail.vendor.com/foo") is True
    assert scheduler._is_blocked_download_url("not a url at all") is False
    assert scheduler._is_blocked_download_url("https://fapiao.jd.com/download/abc.pdf") is False
    assert scheduler._is_blocked_download_url("https://nnfp.jss.com.cn/api/invoice?id=42") is False


@pytest.mark.asyncio
async def test_download_linked_invoice_skips_blocked_host(monkeypatch: pytest.MonkeyPatch) -> None:
    infos: list[str] = []
    monkeypatch.setattr(scheduler.logger, "info", lambda message, *args: infos.append(message % args))
    http_called = {"n": 0}

    class FakeClient:
        async def __aenter__(self):
            http_called["n"] += 1
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url: str):
            raise AssertionError("network should not be hit for blocked URL")

    monkeypatch.setattr(scheduler.httpx, "AsyncClient", lambda **kwargs: FakeClient())

    result = await scheduler._download_linked_invoice("https://linktrace.triggerdelivery.com/abc")
    assert result is None
    assert http_called["n"] == 0
    assert any("Blocked non-invoice URL" in info for info in infos)


@pytest.mark.asyncio
async def test_download_linked_invoice_rejects_non_invoice_content_type(monkeypatch: pytest.MonkeyPatch) -> None:
    infos: list[str] = []
    monkeypatch.setattr(scheduler.logger, "info", lambda message, *args: infos.append(message % args))

    class FakeResponse:
        headers = {"content-type": "text/html; charset=utf-8"}
        content = b"<html>not an invoice</html>"

        def raise_for_status(self) -> None:
            pass

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url: str) -> FakeResponse:
            del url
            return FakeResponse()

    monkeypatch.setattr(scheduler.httpx, "AsyncClient", lambda **kwargs: FakeClient())

    assert await scheduler._download_linked_invoice("https://fapiao.example.com/view?id=1") is None
    assert any("Rejected download" in info and "text/html" in info for info in infos)


@pytest.mark.asyncio
async def test_download_linked_invoice_accepts_pdf_content_type(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        headers = {"content-type": "application/pdf"}
        content = b"%PDF-1.4..."

        def raise_for_status(self) -> None:
            pass

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url: str) -> FakeResponse:
            del url
            return FakeResponse()

    monkeypatch.setattr(scheduler.httpx, "AsyncClient", lambda **kwargs: FakeClient())

    result = await scheduler._download_linked_invoice("https://fapiao.example.com/a.pdf")
    assert result is not None
    assert result[1] == b"%PDF-1.4..."


@pytest.mark.asyncio
async def test_download_linked_invoice_accepts_missing_content_type(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        headers: dict[str, str] = {}
        content = b"%PDF-1.4..."

        def raise_for_status(self) -> None:
            pass

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url: str) -> FakeResponse:
            del url
            return FakeResponse()

    monkeypatch.setattr(scheduler.httpx, "AsyncClient", lambda **kwargs: FakeClient())

    result = await scheduler._download_linked_invoice("https://fapiao.example.com/x.pdf")
    assert result is not None, "absent Content-Type must fall through to filename-based guess"


@pytest.mark.asyncio
async def test_scan_all_accounts_skips_non_invoice_missing_number_and_embedding_failure(
    db, settings, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    settings.sqlite_vec_available = True
    _account = await create_email_account(last_scan_uid=None)
    emails = [
        RawEmail(uid="1", subject="hello", body_text="body", body_html="", from_addr="a@test", received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc), attachments=[]),
        RawEmail(uid="", subject="yes", body_text="body", body_html="", from_addr="a@test", received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc), attachments=[RawAttachment(filename="b.pdf", payload=b"2", content_type="application/pdf")]),
    ]
    parsed_results = iter([ParsedInvoice(invoice_no=None, raw_text="增值税电子普通发票 价税合计 税额 发票号码", confidence=0.9, is_vat_document=True)])

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return emails

    mock_ai_service.analyze_email.return_value = make_analysis(
        is_invoice_related=False,
        invoice_confidence=0.2,
        skip_reason="非发票",
    )
    mock_ai_service.extract_invoice_fields.return_value = mock_ai_service.extract_invoice_fields.return_value.model_copy(
        update={"invoice_no": "", "confidence": 0.2}
    )
    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "parse_invoice", lambda filename, payload: next(parsed_results))
    monkeypatch.setattr(scheduler.FileManager, "save_invoice", AsyncMock(return_value="saved.pdf"))
    monkeypatch.setattr(scheduler, "store_embedding", AsyncMock(side_effect=RuntimeError("embed fail")))

    await scheduler.scan_all_accounts()

    logs = (await db.execute(select(ScanLog))).scalars().all()
    extraction_logs = (await db.execute(select(ExtractionLog).order_by(ExtractionLog.id))).scalars().all()
    assert logs[0].emails_scanned == 2
    assert logs[0].invoices_found == 0
    assert [item.outcome for item in extraction_logs] == ["not_invoice", "low_confidence"]
    assert [item.classification_tier for item in extraction_logs] == [1, 1]
    assert extraction_logs[1].error_detail == "invoice_no missing"


@pytest.mark.asyncio
async def test_scan_all_accounts_embedding_failure_logs_warning(
    db, settings, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    settings.sqlite_vec_available = True
    await create_email_account(last_scan_uid=None)
    email = RawEmail(
        uid="2",
        subject="Document ready",
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
        invoice_type="增值税电子普通发票",
        item_summary="服务费",
        raw_text="增值税电子普通发票 价税合计 税额 发票号码",
        source_format="pdf",
        extraction_method="regex",
        confidence=0.9,
    )
    warnings: list[str] = []

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return [email]

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "parse_invoice", lambda filename, payload: parsed)
    monkeypatch.setattr(scheduler.FileManager, "save_invoice", AsyncMock(return_value="saved.pdf"))
    monkeypatch.setattr(scheduler, "store_embedding", AsyncMock(side_effect=RuntimeError("embed fail")))
    monkeypatch.setattr(scheduler.logger, "warning", lambda message, *args: warnings.append(message % args))

    await scheduler.scan_all_accounts()

    assert any("Embedding failed for invoice INV-EMBED-FAIL" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_scan_all_accounts_downloads_single_invoice_link(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    _account = await create_email_account(last_scan_uid=None)
    email = RawEmail(
        uid="link-2",
        subject="Portal message",
        body_text="Please review the latest portal notice for your account. " * 30,
        body_html="",
        from_addr="sender@test",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[],
        body_links=["https://example.com/file-a", "https://example.com/file-b"],
    )
    parsed = ParsedInvoice(
        invoice_no="INV-LINK-1",
        buyer="Link Buyer",
        seller="Link Seller",
        amount=Decimal("12.00"),
        invoice_date=date(2024, 1, 3),
        invoice_type="增值税电子普通发票",
        item_summary="链接发票",
        raw_text="增值税电子普通发票 价税合计 税额 发票号码",
        source_format="xml",
        extraction_method="xml_xpath",
        confidence=0.9,
    )

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return [email]

    download_calls: list[str] = []

    mock_ai_service.analyze_email.return_value = make_analysis(
        best_download_url="https://example.com/file-a",
        url_confidence=0.95,
        url_kind=UrlKind.DIRECT_FILE,
    )

    async def fake_download(url: str):
        download_calls.append(url)
        return ("download.xml", b"<invoice />")

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "_download_linked_invoice", fake_download)
    monkeypatch.setattr(scheduler, "parse_invoice", lambda filename, payload: parsed)
    monkeypatch.setattr(scheduler.FileManager, "save_invoice", AsyncMock(return_value="linked.xml"))

    await scheduler.scan_all_accounts()

    invoices = (await db.execute(select(Invoice))).scalars().all()
    assert len(invoices) == 1
    assert invoices[0].invoice_no == "INV-LINK-1"
    assert download_calls == ["https://example.com/file-a"]


@pytest.mark.asyncio
async def test_scan_all_accounts_resolves_safelink_before_download(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    await create_email_account(last_scan_uid=None)
    email = RawEmail(
        uid="link-safe-1",
        subject="Portal message",
        body_text="Please review the latest portal notice for your account. " * 30,
        body_html="",
        from_addr="sender@test",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[],
        body_links=["https://safe.example.com"],
    )
    parsed = ParsedInvoice(invoice_no="INV-SAFE-1", raw_text="增值税电子普通发票 价税合计 税额 发票号码", confidence=0.9, invoice_type="增值税电子普通发票")

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return [email]

    download_mock = AsyncMock(return_value=("download.pdf", b"pdf"))
    resolve_mock = AsyncMock(return_value="https://real.example.com/file.pdf")
    mock_ai_service.analyze_email.return_value = make_analysis(
        best_download_url="https://safe.example.com",
        url_confidence=0.9,
        url_is_safelink=True,
        url_kind=UrlKind.SAFELINK_WRAPPED,
    )

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "_resolve_safelink", resolve_mock)
    monkeypatch.setattr(scheduler, "_download_linked_invoice", download_mock)
    monkeypatch.setattr(scheduler, "parse_invoice", lambda filename, payload: parsed)
    monkeypatch.setattr(scheduler.FileManager, "save_invoice", AsyncMock(return_value="saved.pdf"))

    await scheduler.scan_all_accounts()

    resolve_mock.assert_awaited_once_with("https://safe.example.com")
    download_mock.assert_awaited_once_with("https://real.example.com/file.pdf")


@pytest.mark.asyncio
async def test_scan_all_accounts_skips_failed_link_download(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    await create_email_account(last_scan_uid=None)
    email = RawEmail(
        uid="link-fail-1",
        subject="Portal message",
        body_text="Please review the latest portal notice for your account. " * 30,
        body_html="",
        from_addr="sender@test",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[],
        body_links=["https://example.com/file-a"],
    )

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return [email]

    mock_ai_service.analyze_email.return_value = make_analysis(
        best_download_url="https://example.com/file-a",
        url_confidence=0.92,
        url_kind=UrlKind.DIRECT_FILE,
    )
    parse_calls: list[tuple[str, bytes]] = []

    def fake_parse_invoice(filename: str, payload: bytes):
        parse_calls.append((filename, payload))
        raise AssertionError("parse_invoice should not be called when link download fails")

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "_download_linked_invoice", AsyncMock(return_value=None))
    monkeypatch.setattr(scheduler, "parse_invoice", fake_parse_invoice)

    await scheduler.scan_all_accounts()

    invoices = (await db.execute(select(Invoice))).scalars().all()
    logs = (await db.execute(select(ScanLog))).scalars().all()
    assert invoices == []
    assert parse_calls == []
    assert logs[0].emails_scanned == 1
    assert logs[0].invoices_found == 0


@pytest.mark.asyncio
async def test_scan_all_accounts_skips_download_when_best_download_url_missing(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    await create_email_account(last_scan_uid=None)
    email = RawEmail(
        uid="link-none-1",
        subject="Portal message",
        body_text="Please review the latest portal notice for your account. " * 30,
        body_html="",
        from_addr="sender@test",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[],
        body_links=["https://example.com/invoice-portal"],
    )

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return [email]

    mock_ai_service.analyze_email.return_value = make_analysis(best_download_url=None)
    download_mock = AsyncMock(return_value=("download.xml", b"<invoice />"))

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "_download_linked_invoice", download_mock)

    await scheduler.scan_all_accounts()

    download_mock.assert_not_awaited()
    invoices = (await db.execute(select(Invoice))).scalars().all()
    logs = (await db.execute(select(ScanLog))).scalars().all()
    assert invoices == []
    assert logs[0].emails_scanned == 1


@pytest.mark.asyncio
async def test_scan_all_accounts_skips_seen_email_uid_and_attachment_pair(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    _account = await create_email_account(last_scan_uid=None)
    email = RawEmail(
        uid="uid-seen-1",
        subject="Document ready",
        body_text="body",
        body_html="",
        from_addr="sender@test",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[RawAttachment(filename="repeat.pdf", payload=b"1", content_type="application/pdf")],
    )
    parsed = ParsedInvoice(
        invoice_no="INV-SEEN-1",
        buyer="Buyer",
        seller="Seller",
        amount=Decimal("10.00"),
        invoice_date=date(2024, 1, 1),
        invoice_type="增值税电子普通发票",
        raw_text="增值税电子普通发票 价税合计 税额 发票号码",
        source_format="pdf",
        extraction_method="regex",
        confidence=0.9,
    )

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return [email]

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "parse_invoice", lambda filename, payload: parsed)
    monkeypatch.setattr(scheduler.FileManager, "save_invoice", AsyncMock(return_value="saved.pdf"))

    await scheduler.scan_all_accounts()
    await scheduler.scan_all_accounts()

    invoices = (await db.execute(select(Invoice).order_by(Invoice.id.asc()))).scalars().all()
    extraction_logs = (await db.execute(select(ExtractionLog).order_by(ExtractionLog.id.asc()))).scalars().all()
    assert len(invoices) == 1
    assert [item.outcome for item in extraction_logs] == ["saved", "skipped_seen"]


@pytest.mark.asyncio
async def test_scan_all_accounts_logs_low_confidence_even_after_llm_enrichment(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    _account = await create_email_account(last_scan_uid=None)
    email = RawEmail(
        uid="uid-low-1",
        subject="Document ready",
        body_text="body",
        body_html="",
        from_addr="sender@test",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[RawAttachment(filename="low.pdf", payload=b"1", content_type="application/pdf")],
    )
    parsed = ParsedInvoice(invoice_no="INV-LOW-1", raw_text="needs llm", confidence=0.1, is_vat_document=True)
    mock_ai_service.extract_invoice_fields.return_value = mock_ai_service.extract_invoice_fields.return_value.model_copy(
        update={"invoice_no": "INV-LOW-1", "confidence": 0.2}
    )

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return [email]

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "parse_invoice", lambda filename, payload: parsed)

    await scheduler.scan_all_accounts()

    invoices = (await db.execute(select(Invoice))).scalars().all()
    extraction_logs = (await db.execute(select(ExtractionLog))).scalars().all()
    assert invoices == []
    assert extraction_logs[0].outcome == "low_confidence"
    assert extraction_logs[0].confidence == 0.2


@pytest.mark.asyncio
async def test_scan_all_accounts_rejects_llm_flagged_non_vat_invoice(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    await create_email_account(last_scan_uid=None)
    email = RawEmail(
        uid="uid-nonvat-1",
        subject="Receipt",
        body_text="body",
        body_html="",
        from_addr="sender@test",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[RawAttachment(filename="receipt.pdf", payload=b"1", content_type="application/pdf")],
    )
    parsed = ParsedInvoice(invoice_no=None, raw_text="needs llm", confidence=0.1, is_vat_document=True)
    mock_ai_service.extract_invoice_fields.return_value = mock_ai_service.extract_invoice_fields.return_value.model_copy(
        update={"invoice_no": "INV-NONVAT-1", "invoice_type": "酒店入住凭证", "is_valid_tax_invoice": False}
    )

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return [email]

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "parse_invoice", lambda filename, payload: parsed)

    await scheduler.scan_all_accounts()

    invoices = (await db.execute(select(Invoice))).scalars().all()
    extraction_logs = (await db.execute(select(ExtractionLog))).scalars().all()
    assert invoices == []
    assert extraction_logs[0].outcome == "not_vat_invoice"
    assert extraction_logs[0].error_detail == "type='' llm_rejected=True"


@pytest.mark.asyncio
async def test_scan_all_accounts_rejects_heuristic_non_vat_invoice_without_llm(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    await create_email_account(last_scan_uid=None)
    email = RawEmail(
        uid="uid-heuristic-1",
        subject="Receipt",
        body_text="body",
        body_html="",
        from_addr="sender@test",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[RawAttachment(filename="receipt.pdf", payload=b"1", content_type="application/pdf")],
    )
    parsed = ParsedInvoice(
        invoice_no="RCPT-1",
        raw_text="普通收据",
        confidence=0.9,
        invoice_type="酒店收据",
        buyer="Hotel Buyer",
        seller="Hotel Seller",
        amount=Decimal("100.00"),
        invoice_date=date(2024, 1, 1),
        item_summary="住宿费",
    )

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return [email]

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "parse_invoice", lambda filename, payload: parsed)

    await scheduler.scan_all_accounts()

    invoices = (await db.execute(select(Invoice))).scalars().all()
    extraction_logs = (await db.execute(select(ExtractionLog))).scalars().all()
    assert invoices == []
    assert extraction_logs[0].outcome == "not_vat_invoice"
    assert extraction_logs[0].error_detail == "type='酒店收据' llm_rejected=False"


@pytest.mark.asyncio
async def test_scan_all_accounts_marks_amount_sentinel_as_low_confidence(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    await create_email_account(last_scan_uid=None)
    email = RawEmail(
        uid="uid-sentinel-1",
        subject="Invoice",
        body_text="body",
        body_html="",
        from_addr="sender@test",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[RawAttachment(filename="invoice.pdf", payload=b"1", content_type="application/pdf")],
    )
    parsed = ParsedInvoice(
        invoice_no="INV-SENTINEL-1",
        raw_text="增值税电子普通发票 价税合计 税额 发票号码",
        confidence=0.9,
        amount=Decimal("0.01"),
        invoice_type="增值税电子普通发票",
    )
    mock_ai_service.extract_invoice_fields.return_value = mock_ai_service.extract_invoice_fields.return_value.model_copy(
        update={"amount": Decimal("0.01"), "confidence": 0.2}
    )

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return [email]

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "parse_invoice", lambda filename, payload: parsed)

    await scheduler.scan_all_accounts()

    invoices = (await db.execute(select(Invoice))).scalars().all()
    extraction_logs = (await db.execute(select(ExtractionLog))).scalars().all()
    assert invoices == []
    assert extraction_logs[0].outcome == "low_confidence"
    assert extraction_logs[0].error_detail == "amount sentinel"


@pytest.mark.asyncio
async def test_scan_all_accounts_prioritizes_pdf_by_llm_hints(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    await create_email_account(last_scan_uid=None)
    email = RawEmail(
        uid="hint-1",
        subject="Portal documents",
        body_text="body",
        body_html="",
        from_addr="sender@test",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[RawAttachment(filename="invoice.xml", payload=b"xml", content_type="application/xml")],
        body_links=["https://example.com/invoice.pdf"],
    )
    parse_calls: list[str] = []

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return [email]

    def fake_parse_invoice(filename: str, payload: bytes) -> ParsedInvoice:
        del payload
        parse_calls.append(filename)
        if filename.endswith(".pdf"):
            return ParsedInvoice(invoice_no="INV-HINT-1", raw_text="增值税电子普通发票 价税合计 税额 发票号码", confidence=0.9, invoice_type="增值税电子普通发票")
        raise RuntimeError("xml should not be processed first")

    mock_ai_service.analyze_email.return_value = make_analysis(
        best_download_url="https://example.com/invoice.pdf",
        url_confidence=0.9,
        url_kind=UrlKind.DIRECT_FILE,
        extraction_hints={
            "platform": InvoicePlatform.NUONUO,
            "likely_formats": [InvoiceFormat.PDF, InvoiceFormat.XML],
        },
    )

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "_download_linked_invoice", AsyncMock(return_value=("download.pdf", b"pdf")))
    monkeypatch.setattr(scheduler, "parse_invoice", fake_parse_invoice)
    monkeypatch.setattr(scheduler.FileManager, "save_invoice", AsyncMock(return_value="saved.pdf"))

    await scheduler.scan_all_accounts()

    assert parse_calls[0] == "download.pdf"


@pytest.mark.asyncio
async def test_scan_all_accounts_prioritizes_pdf_download_when_no_attachment_strong_positive(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    await create_email_account(last_scan_uid=None)
    email = RawEmail(
        uid="hint-2",
        subject="Invoice ready",
        body_text="请下载发票",
        body_html="",
        from_addr="sender@test",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[RawAttachment(filename="invoice.bin", payload=b"bin", content_type="application/octet-stream")],
        body_links=["https://example.com/invoice.pdf"],
    )
    parse_calls: list[str] = []

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return [email]

    def fake_parse_invoice(filename: str, payload: bytes) -> ParsedInvoice:
        del payload
        parse_calls.append(filename)
        if filename.endswith(".pdf"):
            return ParsedInvoice(invoice_no="INV-HINT-2", raw_text="增值税电子普通发票 价税合计 税额 发票号码", confidence=0.9, invoice_type="增值税电子普通发票")
        raise RuntimeError("binary attachment should not be processed first")

    mock_ai_service.analyze_email.return_value = make_analysis(
        best_download_url="https://example.com/invoice.pdf",
        url_confidence=0.9,
        url_kind=UrlKind.DIRECT_FILE,
        extraction_hints={
            "platform": InvoicePlatform.NUONUO,
            "likely_formats": [InvoiceFormat.PDF, InvoiceFormat.XML],
        },
    )

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "_download_linked_invoice", AsyncMock(return_value=("download.pdf", b"pdf")))
    monkeypatch.setattr(scheduler, "parse_invoice", fake_parse_invoice)
    monkeypatch.setattr(scheduler.FileManager, "save_invoice", AsyncMock(return_value="saved.pdf"))

    await scheduler.scan_all_accounts()

    assert parse_calls[0] == "download.pdf"


@pytest.mark.asyncio
async def test_scan_all_accounts_sends_webhook_with_signature(
    db, settings, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    settings.WEBHOOK_URL = "https://example.com/webhook"
    settings.WEBHOOK_SECRET = "secret-key"
    await create_email_account(last_scan_uid=None)
    email = RawEmail(
        uid="webhook-1",
        subject="Document ready",
        body_text="body",
        body_html="",
        from_addr="sender@test",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[RawAttachment(filename="invoice.pdf", payload=b"pdf", content_type="application/pdf")],
    )
    parsed = ParsedInvoice(
        invoice_no="INV-WEBHOOK-1",
        buyer="Buyer",
        seller="Seller",
        amount=Decimal("10.00"),
        invoice_date=date(2024, 1, 1),
        invoice_type="增值税电子普通发票",
        item_summary="办公用品",
        raw_text="增值税电子普通发票 价税合计 税额 发票号码",
        source_format="pdf",
        extraction_method="regex",
        confidence=0.92,
    )
    captured: dict[str, object] = {}

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return [email]

    class FakeResponse:
        status_code = 202
        is_success = True
        text = "ok"

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url: str, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "parse_invoice", lambda filename, payload: parsed)
    monkeypatch.setattr(scheduler.FileManager, "save_invoice", AsyncMock(return_value="saved.pdf"))
    monkeypatch.setattr(scheduler.httpx, "AsyncClient", lambda **kwargs: FakeClient())

    await scheduler.scan_all_accounts()

    payload = captured["json"]
    assert captured["url"] == settings.WEBHOOK_URL
    assert payload == {
        "event": "invoice.created",
        "invoice_no": "INV-WEBHOOK-1",
        "buyer": "Buyer",
        "seller": "Seller",
        "amount": "10.00",
        "invoice_date": "2024-01-01",
        "invoice_type": "增值税电子普通发票",
        "confidence": 0.92,
    }
    expected_signature = "sha256=" + hmac.new(
        settings.WEBHOOK_SECRET.encode("utf-8"),
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert captured["headers"] == {"X-Signature-256": expected_signature}

    webhook_logs = (await db.execute(select(WebhookLog))).scalars().all()
    assert len(webhook_logs) == 1
    assert webhook_logs[0].invoice_no == "INV-WEBHOOK-1"
    assert webhook_logs[0].status_code == 202
    assert webhook_logs[0].success is True


@pytest.mark.asyncio
async def test_scan_all_accounts_webhook_failure_logs_warning_and_continues(
    db, settings, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    settings.WEBHOOK_URL = "https://example.com/webhook"
    settings.WEBHOOK_SECRET = "secret-key"
    await create_email_account(last_scan_uid=None)
    email = RawEmail(
        uid="webhook-fail-1",
        subject="Document ready",
        body_text="body",
        body_html="",
        from_addr="sender@test",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[RawAttachment(filename="invoice.pdf", payload=b"pdf", content_type="application/pdf")],
    )
    parsed = ParsedInvoice(
        invoice_no="INV-WEBHOOK-FAIL",
        buyer="Buyer",
        seller="Seller",
        amount=Decimal("11.00"),
        invoice_date=date(2024, 1, 2),
        invoice_type="增值税电子普通发票",
        item_summary="办公用品",
        raw_text="增值税电子普通发票 价税合计 税额 发票号码",
        source_format="pdf",
        extraction_method="regex",
        confidence=0.92,
    )
    warnings: list[str] = []

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return [email]

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url: str, json=None, headers=None):
            del url, json, headers
            raise scheduler.httpx.HTTPError("webhook boom")

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "parse_invoice", lambda filename, payload: parsed)
    monkeypatch.setattr(scheduler.FileManager, "save_invoice", AsyncMock(return_value="saved.pdf"))
    monkeypatch.setattr(scheduler.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    monkeypatch.setattr(scheduler.logger, "warning", lambda message, *args: warnings.append(message % args))

    await scheduler.scan_all_accounts()

    invoices = (await db.execute(select(Invoice))).scalars().all()
    webhook_logs = (await db.execute(select(WebhookLog))).scalars().all()
    scan_logs = (await db.execute(select(ScanLog))).scalars().all()
    assert len(invoices) == 1
    assert len(webhook_logs) == 1
    assert webhook_logs[0].success is False
    assert webhook_logs[0].status_code is None
    assert webhook_logs[0].error_detail == "webhook boom"
    assert scan_logs[0].invoices_found == 1
    assert any("Webhook delivery failed for invoice INV-WEBHOOK-FAIL" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_scan_all_accounts_webhook_non_success_response_logs_warning(
    db, settings, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    settings.WEBHOOK_URL = "https://example.com/webhook"
    settings.WEBHOOK_SECRET = "secret-key"
    await create_email_account(last_scan_uid=None)
    email = RawEmail(
        uid="webhook-422-1",
        subject="Document ready",
        body_text="body",
        body_html="",
        from_addr="sender@test",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[RawAttachment(filename="invoice.pdf", payload=b"pdf", content_type="application/pdf")],
    )
    parsed = ParsedInvoice(
        invoice_no="INV-WEBHOOK-422",
        buyer="Buyer",
        seller="Seller",
        amount=Decimal("11.50"),
        invoice_date=date(2024, 1, 2),
        invoice_type="增值税电子普通发票",
        item_summary="办公用品",
        raw_text="增值税电子普通发票 价税合计 税额 发票号码",
        source_format="pdf",
        extraction_method="regex",
        confidence=0.92,
    )
    warnings: list[str] = []

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return [email]

    class FakeResponse:
        status_code = 422
        is_success = False
        text = "invalid payload"

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url: str, json=None, headers=None):
            del url, json, headers
            return FakeResponse()

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "parse_invoice", lambda filename, payload: parsed)
    monkeypatch.setattr(scheduler.FileManager, "save_invoice", AsyncMock(return_value="saved.pdf"))
    monkeypatch.setattr(scheduler.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    monkeypatch.setattr(scheduler.logger, "warning", lambda message, *args: warnings.append(message % args))

    await scheduler.scan_all_accounts()

    webhook_logs = (await db.execute(select(WebhookLog))).scalars().all()
    assert len(webhook_logs) == 1
    assert webhook_logs[0].status_code == 422
    assert webhook_logs[0].success is False
    assert webhook_logs[0].error_detail == "invalid payload"
    assert any("Webhook delivery failed for invoice INV-WEBHOOK-422 with status 422" in warning for warning in warnings)
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "parse_invoice", lambda filename, payload: parsed)
    monkeypatch.setattr(scheduler.FileManager, "save_invoice", AsyncMock(return_value="saved.pdf"))
    monkeypatch.setattr(scheduler.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    monkeypatch.setattr(scheduler.logger, "warning", lambda message, *args: warnings.append(message % args))

    await scheduler.scan_all_accounts()

    webhook_logs = (await db.execute(select(WebhookLog))).scalars().all()
    assert len(webhook_logs) == 1
    assert webhook_logs[0].status_code == 422
    assert webhook_logs[0].success is False
    assert webhook_logs[0].error_detail == "invalid payload"
    assert any("Webhook delivery failed for invoice INV-WEBHOOK-422 with status 422" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_process_single_email_returns_email_result_and_saves_invoice(
    db, settings, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    account = await create_email_account(last_scan_uid=None)
    scan_log = ScanLog(
        user_id=account.user_id, email_account_id=account.id,
        started_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        emails_scanned=0,
        invoices_found=0,
    )
    db.add(scan_log)
    await db.commit()
    await db.refresh(scan_log)
    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(
        scheduler,
        "parse_invoice",
        lambda filename, payload: ParsedInvoice(invoice_no="INV-SINGLE", raw_text="增值税电子普通发票 价税合计 税额 发票号码", confidence=0.9, invoice_type="增值税电子普通发票"),
    )
    monkeypatch.setattr(scheduler.FileManager, "save_invoice", AsyncMock(return_value="saved.pdf"))

    result = await scheduler._process_single_email(
        email_data=RawEmail(
            uid="uid-1",
            subject="Invoice",
            body_text="body",
            body_html="",
            from_addr="sender@test",
            received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            attachments=[RawAttachment(filename="invoice.pdf", payload=b"pdf", content_type="application/pdf")],
        ),
        classifier=SimpleNamespace(classify_tier1=lambda email: SimpleNamespace(is_invoice=True)),
        ai=mock_ai_service,
        file_mgr=scheduler.FileManager(settings.STORAGE_PATH),
        settings=settings,
        log_id=scan_log.id,
        account_id=account.id,
        user_id=account.user_id,
    )

    assert result == scheduler._EmailResult(invoices_added=1, last_uid="uid-1", error=None)


@pytest.mark.asyncio
async def test_process_single_email_handles_invoice_integrity_error(monkeypatch: pytest.MonkeyPatch) -> None:
    added: list[object] = []
    committed: list[bool] = []
    rolled_back: list[bool] = []

    class FakeScalarResult:
        def scalar_one_or_none(self):
            return None

    class FakeDB:
        def add(self, item):
            added.append(item)

        async def commit(self):
            committed.append(True)

        async def rollback(self):
            rolled_back.append(True)

        async def execute(self, stmt):
            del stmt
            return FakeScalarResult()

        async def flush(self):
            raise IntegrityError("insert", {}, Exception("duplicate"))

    async def fake_get_db():
        yield FakeDB()

    monkeypatch.setattr(scheduler, "get_db", fake_get_db)
    monkeypatch.setattr(
        scheduler,
        "parse_invoice",
        lambda filename, payload: ParsedInvoice(invoice_no="INV-RACE", raw_text="增值税电子普通发票 价税合计 税额 发票号码", confidence=0.9, invoice_type="增值税电子普通发票"),
    )

    result = await scheduler._process_single_email(
        email_data=RawEmail(
            uid="uid-race",
            subject="Invoice",
            body_text="body",
            body_html="",
            from_addr="sender@test",
            received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            attachments=[RawAttachment(filename="invoice.pdf", payload=b"pdf", content_type="application/pdf")],
        ),
        classifier=SimpleNamespace(classify_tier1=lambda email: SimpleNamespace(is_invoice=True)),
        ai=SimpleNamespace(
            analyze_email=AsyncMock(),
            extract_invoice_fields=AsyncMock(
                return_value=SimpleNamespace(
                    buyer="Buyer",
                    seller="Seller",
                    invoice_no="INV-RACE",
                    invoice_date=date(2024, 1, 1),
                    amount=Decimal("10.00"),
                    item_summary="办公用品",
                    invoice_type="增值税电子普通发票",
                    confidence=0.9,
                    is_valid_tax_invoice=True,
                )
            ),
            embed_text=AsyncMock(),
        ),
        file_mgr=SimpleNamespace(save_invoice=AsyncMock(return_value="saved.pdf")),
        settings=SimpleNamespace(sqlite_vec_available=False),
        log_id=1,
        account_id=1,
        user_id=1,
    )

    assert result.invoices_added == 0
    assert rolled_back == [True]
    assert committed == [True, True]
    assert any(getattr(item, "outcome", None) == "duplicate" for item in added)


@pytest.mark.asyncio
async def test_process_single_email_returns_error_result_on_outer_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    rolled_back: list[bool] = []

    class FakeDB:
        async def rollback(self):
            rolled_back.append(True)

    async def fake_get_db():
        yield FakeDB()

    monkeypatch.setattr(scheduler, "get_db", fake_get_db)

    result = await scheduler._process_single_email(
        email_data=RawEmail(
            uid="uid-fail",
            subject="Invoice",
            body_text="body",
            body_html="",
            from_addr="sender@test",
            received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            attachments=[],
        ),
        classifier=SimpleNamespace(classify_tier1=lambda email: (_ for _ in ()).throw(RuntimeError("boom"))),
        ai=SimpleNamespace(),
        file_mgr=SimpleNamespace(),
        settings=SimpleNamespace(sqlite_vec_available=False),
        log_id=1,
        account_id=1,
        user_id=1,
    )

    assert result == scheduler._EmailResult(invoices_added=0, last_uid="uid-fail", error="boom")
    assert rolled_back == [True]


@pytest.mark.asyncio
async def test_process_single_email_returns_default_when_no_db_session(monkeypatch: pytest.MonkeyPatch) -> None:
    class EmptyDBIterator:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    def fake_get_db():
        return EmptyDBIterator()

    monkeypatch.setattr(scheduler, "get_db", fake_get_db)

    result = await scheduler._process_single_email(
        email_data=SimpleNamespace(uid="uid-none"),
        classifier=SimpleNamespace(),
        ai=SimpleNamespace(),
        file_mgr=SimpleNamespace(),
        settings=SimpleNamespace(sqlite_vec_available=False),
        log_id=1,
        account_id=1,
        user_id=1,
    )

    assert result == scheduler._EmailResult()


@pytest.mark.asyncio
async def test_scan_all_accounts_counts_task_exception_and_error_result(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    account = await create_email_account(last_scan_uid=None)
    emails = [
        RawEmail(uid="uid-1", subject="A", body_text="body", body_html="", from_addr="a@test", received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc), attachments=[]),
        RawEmail(uid="uid-2", subject="B", body_text="body", body_html="", from_addr="b@test", received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc), attachments=[]),
    ]

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return emails

    call_count = 0

    async def fake_process_single_email(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("task failed")
        return scheduler._EmailResult(invoices_added=0, last_uid=kwargs["email_data"].uid, error="email failed")

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "_process_single_email", fake_process_single_email)

    await scheduler.scan_all_accounts()
    await db.refresh(account)

    progress = sp.get_progress()
    assert progress.errors == 2
    assert progress.emails_processed == 2
    assert account.last_scan_uid in {"uid-1", "uid-2"}


@pytest.mark.asyncio
async def test_scan_all_accounts_skips_webhook_when_disabled(
    db, settings, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    settings.WEBHOOK_URL = ""
    await create_email_account(last_scan_uid=None)
    email = RawEmail(
        uid="webhook-off-1",
        subject="Document ready",
        body_text="body",
        body_html="",
        from_addr="sender@test",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[RawAttachment(filename="invoice.pdf", payload=b"pdf", content_type="application/pdf")],
    )
    parsed = ParsedInvoice(
        invoice_no="INV-WEBHOOK-OFF",
        buyer="Buyer",
        seller="Seller",
        amount=Decimal("12.00"),
        invoice_date=date(2024, 1, 3),
        invoice_type="增值税电子普通发票",
        item_summary="办公用品",
        raw_text="增值税电子普通发票 价税合计 税额 发票号码",
        source_format="pdf",
        extraction_method="regex",
        confidence=0.92,
    )

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return [email]

    post_mock = AsyncMock()

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "parse_invoice", lambda filename, payload: parsed)
    monkeypatch.setattr(scheduler.FileManager, "save_invoice", AsyncMock(return_value="saved.pdf"))
    monkeypatch.setattr(scheduler.httpx.AsyncClient, "post", post_mock, raising=False)

    await scheduler.scan_all_accounts()

    webhook_logs = (await db.execute(select(WebhookLog))).scalars().all()
    assert webhook_logs == []
    post_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_enrichment_fires_when_fields_missing_despite_high_confidence(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    await create_email_account(last_scan_uid=None)
    email = RawEmail(
        uid="uid-enrich-missing",
        subject="Invoice",
        body_text="body",
        body_html="",
        from_addr="sender@test",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[RawAttachment(filename="invoice.pdf", payload=b"1", content_type="application/pdf")],
    )
    parsed = ParsedInvoice(
        invoice_no="12345678901234567890",
        raw_text="增值税电子普通发票 价税合计 税额 发票号码 12345678901234567890",
        confidence=0.7,
        amount=Decimal("100.00"),
        invoice_date=date(2024, 5, 1),
        extraction_method="qr",
        is_vat_document=True,
    )

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return [email]

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "parse_invoice", lambda filename, payload: parsed)
    monkeypatch.setattr(scheduler.FileManager, "save_invoice", AsyncMock(return_value="saved.pdf"))

    await scheduler.scan_all_accounts()

    mock_ai_service.extract_invoice_fields.assert_awaited_once()
    invoices = (await db.execute(select(Invoice))).scalars().all()
    assert len(invoices) == 1
    assert invoices[0].invoice_no == "12345678901234567890"
    assert invoices[0].buyer == "测试购买方"
    assert invoices[0].seller == "测试销售方"
    assert invoices[0].invoice_type == "增值税电子普通发票"
    assert invoices[0].amount == Decimal("100.00")
    assert invoices[0].invoice_date == date(2024, 5, 1)
    assert invoices[0].extraction_method == "llm"


@pytest.mark.asyncio
async def test_parser_invoice_no_wins_over_llm_for_valid_20digit(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    await create_email_account(last_scan_uid=None)
    email = RawEmail(
        uid="uid-parser-wins",
        subject="Invoice",
        body_text="body",
        body_html="",
        from_addr="sender@test",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[RawAttachment(filename="invoice.pdf", payload=b"1", content_type="application/pdf")],
    )
    parsed = ParsedInvoice(
        invoice_no="99988877766655544433",
        raw_text="增值税电子普通发票 价税合计 税额 发票号码",
        confidence=0.7,
        amount=Decimal("200.00"),
        invoice_date=date(2024, 6, 1),
        extraction_method="xml_xpath",
        is_vat_document=True,
    )
    mock_ai_service.extract_invoice_fields.return_value = mock_ai_service.extract_invoice_fields.return_value.model_copy(
        update={"invoice_no": "DIFFERENT-NO-FROM-LLM"}
    )

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return [email]

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "parse_invoice", lambda filename, payload: parsed)
    monkeypatch.setattr(scheduler.FileManager, "save_invoice", AsyncMock(return_value="saved.pdf"))

    await scheduler.scan_all_accounts()

    invoices = (await db.execute(select(Invoice))).scalars().all()
    assert len(invoices) == 1
    assert invoices[0].invoice_no == "99988877766655544433"


@pytest.mark.asyncio
async def test_strong_parse_survives_llm_rejection(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    await create_email_account(last_scan_uid=None)
    email = RawEmail(
        uid="uid-strong-survives",
        subject="Invoice",
        body_text="body",
        body_html="",
        from_addr="sender@test",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[RawAttachment(filename="invoice.pdf", payload=b"1", content_type="application/pdf")],
    )
    parsed = ParsedInvoice(
        invoice_no="11122233344455566677",
        raw_text="增值税电子普通发票 价税合计 税额 发票号码",
        confidence=0.7,
        amount=Decimal("300.00"),
        invoice_date=date(2024, 7, 1),
        invoice_type="增值税电子普通发票",
        extraction_method="qr",
        is_vat_document=True,
    )
    mock_ai_service.extract_invoice_fields.return_value = mock_ai_service.extract_invoice_fields.return_value.model_copy(
        update={"is_valid_tax_invoice": False, "invoice_type": "酒店入住凭证"}
    )

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return [email]

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "parse_invoice", lambda filename, payload: parsed)
    monkeypatch.setattr(scheduler.FileManager, "save_invoice", AsyncMock(return_value="saved.pdf"))

    await scheduler.scan_all_accounts()

    invoices = (await db.execute(select(Invoice))).scalars().all()
    extraction_logs = (await db.execute(select(ExtractionLog))).scalars().all()
    assert len(invoices) == 1
    assert invoices[0].invoice_no == "11122233344455566677"
    assert invoices[0].invoice_type == "增值税电子普通发票"
    assert extraction_logs[0].outcome == "saved"


@pytest.mark.asyncio
async def test_llm_exception_falls_back_to_parser_result(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    await create_email_account(last_scan_uid=None)
    email = RawEmail(
        uid="uid-llm-fail",
        subject="Invoice",
        body_text="body",
        body_html="",
        from_addr="sender@test",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[RawAttachment(filename="invoice.pdf", payload=b"1", content_type="application/pdf")],
    )
    parsed = ParsedInvoice(
        invoice_no="55566677788899900011",
        raw_text="增值税电子普通发票 价税合计 税额 发票号码",
        confidence=0.7,
        amount=Decimal("400.00"),
        invoice_date=date(2024, 8, 1),
        invoice_type="增值税电子普通发票",
        extraction_method="qr",
        is_vat_document=True,
    )
    mock_ai_service.extract_invoice_fields.side_effect = RuntimeError("LLM timeout")

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return [email]

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "parse_invoice", lambda filename, payload: parsed)
    monkeypatch.setattr(scheduler.FileManager, "save_invoice", AsyncMock(return_value="saved.pdf"))

    await scheduler.scan_all_accounts()

    invoices = (await db.execute(select(Invoice))).scalars().all()
    assert len(invoices) == 1
    assert invoices[0].invoice_no == "55566677788899900011"
    assert invoices[0].buyer == "未知"
    assert invoices[0].extraction_method == "qr"


@pytest.mark.asyncio
async def test_weak_parse_llm_backfills_all_fields(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    await create_email_account(last_scan_uid=None)
    email = RawEmail(
        uid="uid-weak-parse",
        subject="Invoice",
        body_text="body",
        body_html="",
        from_addr="sender@test",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[RawAttachment(filename="invoice.pdf", payload=b"1", content_type="application/pdf")],
    )
    parsed = ParsedInvoice(
        invoice_no=None,
        raw_text="增值税电子普通发票 价税合计 税额 发票号码",
        confidence=0.1,
        extraction_method="regex",
        is_vat_document=True,
    )

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return [email]

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "parse_invoice", lambda filename, payload: parsed)
    monkeypatch.setattr(scheduler.FileManager, "save_invoice", AsyncMock(return_value="saved.pdf"))

    await scheduler.scan_all_accounts()

    invoices = (await db.execute(select(Invoice))).scalars().all()
    assert len(invoices) == 1
    assert invoices[0].invoice_no == "INV-LLM-001"
    assert invoices[0].buyer == "测试购买方"
    assert invoices[0].amount == Decimal("88.88")
    assert invoices[0].extraction_method == "llm"


@pytest.mark.asyncio
async def test_llm_returns_unknown_does_not_overwrite_parser_semantic_fields(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    await create_email_account(last_scan_uid=None)
    email = RawEmail(
        uid="uid-unknown-preserve",
        subject="Invoice",
        body_text="body",
        body_html="",
        from_addr="sender@test",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[RawAttachment(filename="invoice.pdf", payload=b"1", content_type="application/pdf")],
    )
    parsed = ParsedInvoice(
        invoice_no="77788899900011122233",
        buyer="Parser Buyer",
        seller="Parser Seller",
        invoice_type="增值税电子普通发票",
        raw_text="增值税电子普通发票 价税合计 税额 发票号码",
        confidence=0.7,
        amount=Decimal("99.00"),
        invoice_date=date(2024, 9, 1),
        extraction_method="qr",
        is_vat_document=True,
    )
    mock_ai_service.extract_invoice_fields.return_value = mock_ai_service.extract_invoice_fields.return_value.model_copy(
        update={
            "buyer": "未知",
            "seller": "未知",
            "invoice_type": "未知",
            "item_summary": "未知",
            "invoice_no": "",
            "invoice_date": None,
            "amount": Decimal("0.01"),
            "confidence": 0.3,
            "is_valid_tax_invoice": True,
        }
    )

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return [email]

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "parse_invoice", lambda filename, payload: parsed)
    monkeypatch.setattr(scheduler.FileManager, "save_invoice", AsyncMock(return_value="saved.pdf"))

    await scheduler.scan_all_accounts()

    invoices = (await db.execute(select(Invoice))).scalars().all()
    assert len(invoices) == 1
    assert invoices[0].invoice_no == "77788899900011122233"
    assert invoices[0].buyer == "Parser Buyer"
    assert invoices[0].seller == "Parser Seller"
    assert invoices[0].invoice_type == "增值税电子普通发票"
    assert invoices[0].item_summary == ""
    assert invoices[0].amount == Decimal("99.00")
    assert invoices[0].invoice_date == date(2024, 9, 1)


@pytest.mark.asyncio
async def test_scheduler_hydrates_all_unhydrated_emails_before_classification(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    """Regression test for v0.7.10 hotfix: unhydrated emails (e.g. from IMAP
    scanners that return headers_only=True) must ALWAYS be hydrated before
    the tier-1 classifier runs — not lazily after tier-1 "approves" them.
    The old behaviour caused 100 % of QQ IMAP emails to be rejected pre-
    hydration, leading to 0 invoices saved from 103,732 emails in production.
    """
    await create_email_account(last_scan_uid=None)

    email_without_subject_keyword = RawEmail(
        uid="no-kw-1",
        subject="2024-01-01 monthly billing summary",
        body_text="",
        body_html="",
        from_addr="billing@example.com",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[],
        body_links=[],
        is_hydrated=False,
    )
    email_with_subject_keyword = RawEmail(
        uid="kw-1",
        subject="发票来了",
        body_text="",
        body_html="",
        from_addr="billing@company.com",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[],
        body_links=[],
        is_hydrated=False,
    )

    hydrate_calls: list[str] = []

    parsed_by_uid = {
        "no-kw-1": ParsedInvoice(
            invoice_no="11111111111111111111",
            buyer="Buyer A",
            seller="Seller A",
            amount=Decimal("10.00"),
            invoice_date=date(2024, 1, 1),
            invoice_type="增值税电子普通发票",
            item_summary="服务费A",
            raw_text="增值税电子普通发票 价税合计 税额 发票号码",
            source_format="pdf",
            confidence=0.9,
            extraction_method="qr",
        ),
        "kw-1": ParsedInvoice(
            invoice_no="22222222222222222222",
            buyer="Buyer B",
            seller="Seller B",
            amount=Decimal("20.00"),
            invoice_date=date(2024, 1, 2),
            invoice_type="增值税电子普通发票",
            item_summary="服务费B",
            raw_text="增值税电子普通发票 价税合计 税额 发票号码",
            source_format="pdf",
            confidence=0.9,
            extraction_method="qr",
        ),
    }
    current_uid = {"value": ""}

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid, options
            return [email_without_subject_keyword, email_with_subject_keyword]

        async def hydrate_email(self, account, email):
            del account
            hydrate_calls.append(email.uid)
            email.body_text = "增值税电子普通发票 价税合计 税额 发票号码"
            email.attachments = [
                RawAttachment(filename=f"invoice-{email.uid}.pdf", content_type="application/pdf", payload=b"pdf"),
            ]
            email.is_hydrated = True
            current_uid["value"] = email.uid
            return email

    def _parse(filename, payload):
        del payload
        for uid, parsed in parsed_by_uid.items():
            if uid in filename:
                return parsed
        return parsed_by_uid["no-kw-1"]

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "parse_invoice", _parse)
    monkeypatch.setattr(scheduler.FileManager, "save_invoice", AsyncMock(return_value="saved.pdf"))
    monkeypatch.setattr(scheduler, "store_embedding", AsyncMock(return_value=None))

    await scheduler.scan_all_accounts()

    assert set(hydrate_calls) == {"no-kw-1", "kw-1"}, (
        "Both emails must be hydrated before tier-1 runs — this is the v0.7.10 fix. "
        "Previously, 'no-kw-1' would have been rejected by tier-1 at line 132-135 of "
        "email_classifier.py for 'no content or keywords', which blocked hydration. "
        f"Got: {hydrate_calls}"
    )

    invoices = (await db.execute(select(Invoice))).scalars().all()
    invoice_nos = {i.invoice_no for i in invoices}
    assert invoice_nos == {"11111111111111111111", "22222222222222222222"}, (
        f"expected both emails to save distinct invoices (the no-keyword one "
        f"'no-kw-1' is the regression-test bellwether for v0.7.10); got {invoice_nos}"
    )


@pytest.mark.asyncio
async def test_scheduler_skips_hydration_for_already_hydrated_emails(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    """Emails that the scanner already hydrated (POP3 which eagerly fetches full
    body, or Outlook when bodyPreview is sufficient) must NOT be re-hydrated."""
    await create_email_account(last_scan_uid=None)

    already_hydrated_email = RawEmail(
        uid="hyd-1",
        subject="statement 2024-01",
        body_text="some plain text body",
        body_html="",
        from_addr="billing@example.com",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[],
        body_links=[],
        is_hydrated=True,
    )

    hydrate_calls: list[str] = []

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid, options
            return [already_hydrated_email]

        async def hydrate_email(self, account, email):
            del account
            hydrate_calls.append(email.uid)
            return email

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())

    await scheduler.scan_all_accounts()

    assert hydrate_calls == [], f"already-hydrated email should not be re-hydrated; got {hydrate_calls}"


@pytest.mark.asyncio
async def test_scheduler_rejects_scam_after_llm_merge(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    await create_email_account(last_scan_uid=None)
    email = RawEmail(
        uid="scam-1",
        subject="发票已开具",
        body_text="please check attachment",
        body_html="",
        from_addr="sender@company.com",
        received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        attachments=[RawAttachment(filename="invoice.pdf", content_type="application/pdf", payload=b"pdf")],
    )
    parsed = ParsedInvoice(
        invoice_no="88834814",
        raw_text="增值税电子普通发票 价税合计 税额 发票号码 88834814",
        confidence=0.7,
        amount=Decimal("1.07"),
        invoice_date=date(2024, 1, 1),
        invoice_type="增值税电子普通发票",
        extraction_method="regex",
        is_vat_document=True,
    )
    mock_ai_service.extract_invoice_fields.return_value = mock_ai_service.extract_invoice_fields.return_value.model_copy(
        update={
            "buyer": "代开各行业发票联系微信gn81186",
            "seller": "摩拜出行服务有限公司",
            "invoice_no": "88834814",
            "invoice_type": "增值税电子普通发票",
            "amount": Decimal("1.07"),
            "confidence": 0.9,
            "is_valid_tax_invoice": True,
        }
    )

    class FakeScanner:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return [email]

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "parse_invoice", lambda filename, payload: parsed)

    await scheduler.scan_all_accounts()

    invoices = (await db.execute(select(Invoice))).scalars().all()
    extraction_logs = (await db.execute(select(ExtractionLog))).scalars().all()
    assert invoices == []
    scam_logs = [log for log in extraction_logs if log.outcome == "not_vat_invoice"]
    assert len(scam_logs) == 1
    assert "scam signal" in (scam_logs[0].error_detail or "")


@pytest.mark.asyncio
async def test_scheduler_persists_scanner_last_scan_state(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch
) -> None:
    await create_email_account(last_scan_uid=None)

    class FakeScannerWithState:
        _last_scan_state = '{"INBOX": {"uid": "999", "uidvalidity": "123"}}'

        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return []

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScannerWithState())
    monkeypatch.setattr(scheduler, "AIService", lambda settings: MagicMock())

    await scheduler.scan_all_accounts()

    result = await db.execute(select(EmailAccount))
    account = result.scalars().first()
    assert account.last_scan_uid == '{"INBOX": {"uid": "999", "uidvalidity": "123"}}'


@pytest.mark.asyncio
async def test_scheduler_falls_back_to_email_uid_if_no_scan_state(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch, mock_ai_service
) -> None:
    await create_email_account(last_scan_uid=None)

    class FakeScannerNoState:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return [
                RawEmail(
                    uid="uid-42",
                    subject="test",
                    body_text="",
                    body_html="",
                    from_addr="a@test",
                    received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
                )
            ]

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScannerNoState())
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)

    await scheduler.scan_all_accounts()

    result = await db.execute(select(EmailAccount))
    account = result.scalars().first()
    assert account.last_scan_uid == "uid-42"


@pytest.mark.asyncio
async def test_scheduler_scan_state_unchanged_does_not_update(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing_state = '{"INBOX": {"uid": "42", "uidvalidity": "123"}}'
    await create_email_account(last_scan_uid=existing_state)

    class FakeScannerSameState:
        _last_scan_state = '{"INBOX": {"uid": "42", "uidvalidity": "123"}}'

        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            return []

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScannerSameState())
    monkeypatch.setattr(scheduler, "AIService", lambda settings: MagicMock())

    await scheduler.scan_all_accounts()

    result = await db.execute(select(EmailAccount))
    account = result.scalars().first()
    assert account.last_scan_uid == existing_state


@pytest.mark.asyncio
async def test_scheduler_passes_options_to_scanner(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.email_scanner import ScanOptions

    await create_email_account(last_scan_uid=None)
    captured: list = []

    class FakeScannerCapturesOpts:
        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid
            captured.append(options)
            return []

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda t: FakeScannerCapturesOpts())
    monkeypatch.setattr(scheduler, "AIService", lambda s: MagicMock())

    opts = ScanOptions(unread_only=True, since=None)
    await scheduler.scan_all_accounts(options=opts)

    assert len(captured) == 1
    assert captured[0] is opts
    assert captured[0].unread_only is True


@pytest.mark.asyncio
async def test_scheduler_stamps_orphan_scan_logs_on_next_scan_start(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When a previous scan crashed without stamping finished_at, the next scan
    attempt should immediately clean up those orphan scan_log rows so operators
    aren't left with phantom 'running' scans."""
    import datetime as _dt
    from app.models import ScanLog

    await create_email_account(last_scan_uid=None)
    orphan = ScanLog(
        user_id=1, email_account_id=1,
        started_at=_dt.datetime(2026, 4, 1, 10, 0, 0, tzinfo=_dt.timezone.utc),
        finished_at=None,
        error_message=None,
        emails_scanned=0,
        invoices_found=0,
    )
    db.add(orphan)
    await db.commit()
    await db.refresh(orphan)
    orphan_id = orphan.id

    class FakeNoopScanner:
        _last_scan_state = None

        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid, options
            return []

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda t: FakeNoopScanner())
    monkeypatch.setattr(scheduler, "AIService", lambda s: MagicMock())

    await scheduler.scan_all_accounts()

    db.expire_all()
    result = await db.execute(select(ScanLog).where(ScanLog.id == orphan_id))
    cleaned = result.scalar_one()
    assert cleaned.finished_at is not None, "orphan row must now have finished_at stamped"
    assert cleaned.error_message is not None
    assert "orphan" in (cleaned.error_message or "").lower()


@pytest.mark.asyncio
async def test_scheduler_publishes_progress_callbacks_from_scanner(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scheduler should pass a progress_callback to scanners that support it
    and route the updates through sp.update_progress from the scanner's threadpool thread."""
    await create_email_account(last_scan_uid=None)

    class ProgressPublishingScanner:
        _last_scan_state = None

        async def scan(self, account, last_uid=None, options=None, progress_callback=None):
            del account, last_uid, options
            if progress_callback is not None:
                progress_callback({"total_folders": 3, "current_folder_idx": 1, "folder_fetch_msg": "test"})
            return []

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda t: ProgressPublishingScanner())
    monkeypatch.setattr(scheduler, "AIService", lambda s: MagicMock())

    await scheduler.scan_all_accounts()


@pytest.mark.asyncio
async def test_scheduler_falls_back_when_scanner_lacks_progress_callback_kwarg(
    db, create_email_account, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Older scanners without progress_callback kwarg must still work (TypeError fallback)."""
    await create_email_account(last_scan_uid=None)

    class LegacyScanner:
        _last_scan_state = None

        async def scan(self, account, last_uid=None, options=None):
            del account, last_uid, options
            return []

    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda t: LegacyScanner())
    monkeypatch.setattr(scheduler, "AIService", lambda s: MagicMock())

    await scheduler.scan_all_accounts()


# ══ Phase 0 maintenance jobs: LLM cache TTL eviction + ExtractionLog retention ═══


async def test_cleanup_llm_cache_removes_expired_entries(db, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nightly job must delete rows whose ``expires_at`` is in the past,
    keep rows whose ``expires_at`` is in the future, and keep rows where
    ``expires_at`` is NULL (defense against un-backfilled legacy entries)."""
    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    now = datetime.now(timezone.utc)
    past = now - timedelta(days=1)
    future = now + timedelta(days=30)

    db.add_all([
        LLMCache(
            content_hash="expired-hash-1", prompt_type="classify",
            response_json="{}", expires_at=past,
        ),
        LLMCache(
            content_hash="expired-hash-2", prompt_type="extract",
            response_json="{}", expires_at=past,
        ),
        LLMCache(
            content_hash="fresh-hash-1", prompt_type="classify",
            response_json="{}", expires_at=future,
        ),
        LLMCache(
            content_hash="legacy-hash-no-expiry", prompt_type="extract",
            response_json="{}", expires_at=None,
        ),
    ])
    await db.commit()

    deleted = await scheduler.cleanup_llm_cache()
    assert deleted == 2

    remaining_hashes = {
        row.content_hash for row in (await db.execute(select(LLMCache))).scalars().all()
    }
    assert remaining_hashes == {"fresh-hash-1", "legacy-hash-no-expiry"}


async def test_cleanup_llm_cache_is_no_op_when_nothing_expired(db, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    db.add(LLMCache(
        content_hash="still-fresh", prompt_type="extract",
        response_json="{}", expires_at=future,
    ))
    await db.commit()

    deleted = await scheduler.cleanup_llm_cache()
    assert deleted == 0
    assert (await db.execute(select(LLMCache))).scalars().all()


async def test_cleanup_extraction_logs_removes_old_entries(db, monkeypatch: pytest.MonkeyPatch) -> None:
    """ExtractionLog rows older than ``EXTRACTION_LOG_RETENTION_DAYS`` are
    deleted; recent rows are preserved. Protects SQLite from unbounded log
    growth (the ratio is ~1100 extraction logs per saved invoice, so even
    a small deployment accumulates hundreds of thousands of rows quickly)."""
    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    now = datetime.now(timezone.utc)
    retention = scheduler.EXTRACTION_LOG_RETENTION_DAYS
    very_old = now - timedelta(days=retention + 10)
    recent = now - timedelta(days=1)

    account = EmailAccount(
        user_id=1, name="t", type="imap", host="h", port=143,
        username="u", password_encrypted="pw",
    )
    db.add(account)
    await db.flush()

    scan = ScanLog(user_id=account.user_id, email_account_id=account.id, started_at=now, emails_scanned=1)
    db.add(scan)
    await db.flush()

    db.add_all([
        ExtractionLog(user_id=scan.user_id, scan_log_id=scan.id, email_subject="old-1", outcome="saved", created_at=very_old),
        ExtractionLog(user_id=scan.user_id, scan_log_id=scan.id, email_subject="old-2", outcome="skipped", created_at=very_old),
        ExtractionLog(user_id=scan.user_id, scan_log_id=scan.id, email_subject="recent-1", outcome="saved", created_at=recent),
    ])
    await db.commit()

    deleted = await scheduler.cleanup_extraction_logs()
    assert deleted == 2

    remaining_subjects = {
        r.email_subject for r in (await db.execute(select(ExtractionLog))).scalars().all()
    }
    assert remaining_subjects == {"recent-1"}


async def test_cleanup_extraction_logs_is_no_op_when_nothing_expired(
    db, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No rows older than the retention window: cleanup returns 0 and
    does not log (the `deleted > 0` guard suppresses the per-tick INFO
    line on empty runs to keep logs quiet)."""
    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))

    now = datetime.now(timezone.utc)
    account = EmailAccount(
        user_id=1, name="t", type="imap", host="h", port=143,
        username="u", password_encrypted="pw",
    )
    db.add(account)
    await db.flush()
    scan = ScanLog(user_id=account.user_id, email_account_id=account.id, started_at=now, emails_scanned=1)
    db.add(scan)
    await db.flush()
    db.add(ExtractionLog(
        user_id=scan.user_id, scan_log_id=scan.id, email_subject="fresh",
        outcome="saved", created_at=now - timedelta(days=1),
    ))
    await db.commit()

    deleted = await scheduler.cleanup_extraction_logs()
    assert deleted == 0
    assert (await db.execute(select(ExtractionLog))).scalars().all()


async def test_cleanup_extraction_logs_respects_batch_size(
    db, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One cleanup tick must not delete more than ``EXTRACTION_LOG_CLEANUP_BATCH_SIZE``
    rows per run — protects the scheduler tick from running hot on a large
    first-time cleanup. Subsequent ticks finish the job."""
    monkeypatch.setattr(scheduler, "get_db", make_get_db_override(db))
    monkeypatch.setattr(scheduler, "EXTRACTION_LOG_CLEANUP_BATCH_SIZE", 2)

    now = datetime.now(timezone.utc)
    very_old = now - timedelta(days=scheduler.EXTRACTION_LOG_RETENTION_DAYS + 5)

    account = EmailAccount(
        user_id=1, name="t", type="imap", host="h", port=143,
        username="u", password_encrypted="pw",
    )
    db.add(account)
    await db.flush()
    scan = ScanLog(user_id=account.user_id, email_account_id=account.id, started_at=now, emails_scanned=1)
    db.add(scan)
    await db.flush()

    db.add_all([
        ExtractionLog(
            user_id=scan.user_id, scan_log_id=scan.id, email_subject=f"old-{i}",
            outcome="saved", created_at=very_old,
        )
        for i in range(5)
    ])
    await db.commit()

    first = await scheduler.cleanup_extraction_logs()
    assert first == 2

    remaining = (await db.execute(select(ExtractionLog))).scalars().all()
    assert len(remaining) == 3


def test_ai_service_cache_expiry_windows_match_migration_contract(settings) -> None:
    """Per-prompt-type TTL contract in ``AIService._cache_expiry``.

    classify / analyze_email_v3 → 7 days (shortened in v0.9.1 from 30 days
    to limit false-negative propagation). Migration 0009's 30-day backfill
    for pre-existing rows is intentionally left at the legacy value so old
    entries age out on their original schedule — new entries are written
    with the shorter 7-day window going forward.

    extract → 365 days (unchanged; invoice PDF content is effectively
    immutable once captured, so re-paying for OCR is waste)."""
    from app.services.ai_service import AIService

    service = AIService(settings)
    now = datetime.now(timezone.utc)

    classify_exp = service._cache_expiry("classify")
    analyze_exp = service._cache_expiry("analyze_email_v3")
    extract_exp = service._cache_expiry("extract")

    classify_days = (classify_exp - now).total_seconds() / 86400
    analyze_days = (analyze_exp - now).total_seconds() / 86400
    extract_days = (extract_exp - now).total_seconds() / 86400

    assert 6.99 < classify_days < 7.01
    assert 6.99 < analyze_days < 7.01
    assert 364.99 < extract_days < 365.01


async def test_get_cache_treats_expired_rows_as_miss(db, settings) -> None:
    """The cache read path must filter out rows whose ``expires_at`` is in
    the past. Without this filter, expired entries would serve stale data
    even after the cleanup job has run (between cleanup ticks, or for rows
    that expired seconds ago)."""
    from app.services.ai_service import AIService
    now = datetime.now(timezone.utc)

    db.add_all([
        LLMCache(
            content_hash="abc", prompt_type="extract", response_json='{"v": 1}',
            expires_at=now - timedelta(seconds=1),
        ),
        LLMCache(
            content_hash="def", prompt_type="extract", response_json='{"v": 2}',
            expires_at=now + timedelta(days=1),
        ),
        LLMCache(
            content_hash="ghi", prompt_type="extract", response_json='{"v": 3}',
            expires_at=None,
        ),
    ])
    await db.commit()

    service = AIService(settings)
    assert await service._get_cache(db, "abc") is None
    assert await service._get_cache(db, "def") == '{"v": 2}'
    assert await service._get_cache(db, "ghi") == '{"v": 3}'


async def test_set_cache_stamps_appropriate_expires_at(db, settings) -> None:
    """New entries written via ``_set_cache`` must carry an ``expires_at``
    derived from the prompt_type. Without this, new rows written under
    v0.8.10 would remain with NULL ``expires_at`` and never get evicted."""
    from app.services.ai_service import AIService

    service = AIService(settings)
    await service._set_cache(db, "new-classify", "classify", '{"x": 1}')
    await service._set_cache(db, "new-extract", "extract", '{"x": 2}')
    await db.commit()

    now = datetime.now(timezone.utc)
    rows = {
        r.content_hash: r
        for r in (await db.execute(select(LLMCache))).scalars().all()
    }
    assert rows["new-classify"].expires_at is not None
    assert rows["new-extract"].expires_at is not None

    def _to_aware(dt: datetime) -> datetime:
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

    classify_days = (_to_aware(rows["new-classify"].expires_at) - now).total_seconds() / 86400
    extract_days = (_to_aware(rows["new-extract"].expires_at) - now).total_seconds() / 86400
    assert 6.9 < classify_days < 7.1
    assert 364.9 < extract_days < 365.1
