from __future__ import annotations

import hashlib
import hmac
import json
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

import app.tasks.scheduler as scheduler
from app.models import ExtractionLog, Invoice, ScanLog, WebhookLog
from app.schemas.invoice import EmailAnalysis, InvoiceFormat, InvoicePlatform, UrlKind
from app.services import scan_progress as sp
from app.services.email_scanner import RawAttachment, RawEmail
from app.services.invoice_parser import ParsedInvoice


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
    extraction_logs = (await db.execute(select(ExtractionLog))).scalars().all()
    assert len(invoices) == 1
    assert invoices[0].invoice_no == "INV-SCHED-1"
    assert account.last_scan_uid == "2"
    assert logs[0].invoices_found == 1
    assert extraction_logs[0].outcome == "saved"
    assert extraction_logs[0].classification_tier == 1


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
    parsed = ParsedInvoice(invoice_no="INV-TIER1", raw_text="raw", confidence=0.9)

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
        async def scan(self, account, last_uid=None):
            del account, last_uid
            return [email]

    async def override_get_db():
        yield db

    monkeypatch.setattr(scheduler, "get_db", override_get_db)
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
    extraction_logs = (await db.execute(select(ExtractionLog).order_by(ExtractionLog.id))).scalars().all()
    assert len(invoices) == 2
    assert invoices[-1].invoice_no == mock_ai_service.extract_invoice_fields.return_value.invoice_no
    assert invoices[-1].extraction_method == "llm"
    assert logs[0].invoices_found == 1
    assert [item.outcome for item in extraction_logs] == ["duplicate", "saved", "parse_error"]


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
    assert scheduler.get_scheduler() is existing
    scheduler.start_scheduler(settings)
    assert scheduler._scheduler is existing
    scheduler.stop_scheduler()
    assert captured["shutdown_wait"] is False
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
    parsed_results = iter([ParsedInvoice(invoice_no=None, raw_text="raw", confidence=0.9)])

    class FakeScanner:
        async def scan(self, account, last_uid=None):
            del account, last_uid
            return emails

    async def override_get_db():
        yield db

    mock_ai_service.analyze_email.return_value = make_analysis(
        is_invoice_related=False,
        invoice_confidence=0.2,
        skip_reason="非发票",
    )
    monkeypatch.setattr(scheduler, "get_db", override_get_db)
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
        invoice_type="电子普通发票",
        item_summary="链接发票",
        raw_text="raw",
        source_format="xml",
        extraction_method="xml_xpath",
        confidence=0.9,
    )

    class FakeScanner:
        async def scan(self, account, last_uid=None):
            del account, last_uid
            return [email]

    async def override_get_db():
        yield db

    download_calls: list[str] = []

    mock_ai_service.analyze_email.return_value = make_analysis(
        best_download_url="https://example.com/file-a",
        url_confidence=0.95,
        url_kind=UrlKind.DIRECT_FILE,
    )

    async def fake_download(url: str):
        download_calls.append(url)
        return ("download.xml", b"<invoice />")

    monkeypatch.setattr(scheduler, "get_db", override_get_db)
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
    parsed = ParsedInvoice(invoice_no="INV-SAFE-1", raw_text="raw", confidence=0.9)

    class FakeScanner:
        async def scan(self, account, last_uid=None):
            del account, last_uid
            return [email]

    async def override_get_db():
        yield db

    download_mock = AsyncMock(return_value=("download.pdf", b"pdf"))
    resolve_mock = AsyncMock(return_value="https://real.example.com/file.pdf")
    mock_ai_service.analyze_email.return_value = make_analysis(
        best_download_url="https://safe.example.com",
        url_confidence=0.9,
        url_is_safelink=True,
        url_kind=UrlKind.SAFELINK_WRAPPED,
    )

    monkeypatch.setattr(scheduler, "get_db", override_get_db)
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
        async def scan(self, account, last_uid=None):
            del account, last_uid
            return [email]

    async def override_get_db():
        yield db

    mock_ai_service.analyze_email.return_value = make_analysis(
        best_download_url="https://example.com/file-a",
        url_confidence=0.92,
        url_kind=UrlKind.DIRECT_FILE,
    )
    parse_calls: list[tuple[str, bytes]] = []

    def fake_parse_invoice(filename: str, payload: bytes):
        parse_calls.append((filename, payload))
        raise AssertionError("parse_invoice should not be called when link download fails")

    monkeypatch.setattr(scheduler, "get_db", override_get_db)
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
        async def scan(self, account, last_uid=None):
            del account, last_uid
            return [email]

    async def override_get_db():
        yield db

    mock_ai_service.analyze_email.return_value = make_analysis(best_download_url=None)
    download_mock = AsyncMock(return_value=("download.xml", b"<invoice />"))

    monkeypatch.setattr(scheduler, "get_db", override_get_db)
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
        invoice_type="电子普通发票",
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
    parsed = ParsedInvoice(invoice_no="INV-LOW-1", raw_text="needs llm", confidence=0.1)
    mock_ai_service.extract_invoice_fields.return_value = mock_ai_service.extract_invoice_fields.return_value.model_copy(
        update={"invoice_no": "INV-LOW-1", "confidence": 0.2}
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

    await scheduler.scan_all_accounts()

    invoices = (await db.execute(select(Invoice))).scalars().all()
    extraction_logs = (await db.execute(select(ExtractionLog))).scalars().all()
    assert invoices == []
    assert extraction_logs[0].outcome == "low_confidence"
    assert extraction_logs[0].confidence == 0.2


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
        async def scan(self, account, last_uid=None):
            del account, last_uid
            return [email]

    async def override_get_db():
        yield db

    def fake_parse_invoice(filename: str, payload: bytes) -> ParsedInvoice:
        del payload
        parse_calls.append(filename)
        if filename.endswith(".pdf"):
            return ParsedInvoice(invoice_no="INV-HINT-1", raw_text="raw", confidence=0.9)
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

    monkeypatch.setattr(scheduler, "get_db", override_get_db)
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
        async def scan(self, account, last_uid=None):
            del account, last_uid
            return [email]

    async def override_get_db():
        yield db

    def fake_parse_invoice(filename: str, payload: bytes) -> ParsedInvoice:
        del payload
        parse_calls.append(filename)
        if filename.endswith(".pdf"):
            return ParsedInvoice(invoice_no="INV-HINT-2", raw_text="raw", confidence=0.9)
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

    monkeypatch.setattr(scheduler, "get_db", override_get_db)
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
        invoice_type="电子普通发票",
        item_summary="办公用品",
        raw_text="raw",
        source_format="pdf",
        extraction_method="regex",
        confidence=0.92,
    )
    captured: dict[str, object] = {}

    class FakeScanner:
        async def scan(self, account, last_uid=None):
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

    async def override_get_db():
        yield db

    monkeypatch.setattr(scheduler, "get_db", override_get_db)
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
        "invoice_type": "电子普通发票",
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
        invoice_type="电子普通发票",
        item_summary="办公用品",
        raw_text="raw",
        source_format="pdf",
        extraction_method="regex",
        confidence=0.92,
    )
    warnings: list[str] = []

    class FakeScanner:
        async def scan(self, account, last_uid=None):
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

    async def override_get_db():
        yield db

    monkeypatch.setattr(scheduler, "get_db", override_get_db)
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
        invoice_type="电子普通发票",
        item_summary="办公用品",
        raw_text="raw",
        source_format="pdf",
        extraction_method="regex",
        confidence=0.92,
    )
    warnings: list[str] = []

    class FakeScanner:
        async def scan(self, account, last_uid=None):
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

    async def override_get_db():
        yield db

    monkeypatch.setattr(scheduler, "get_db", override_get_db)
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
        invoice_type="电子普通发票",
        item_summary="办公用品",
        raw_text="raw",
        source_format="pdf",
        extraction_method="regex",
        confidence=0.92,
    )

    class FakeScanner:
        async def scan(self, account, last_uid=None):
            del account, last_uid
            return [email]

    async def override_get_db():
        yield db

    post_mock = AsyncMock()

    monkeypatch.setattr(scheduler, "get_db", override_get_db)
    monkeypatch.setattr(scheduler, "AIService", lambda settings: mock_ai_service)
    monkeypatch.setattr(scheduler.ScannerFactory, "get_scanner", lambda account_type: FakeScanner())
    monkeypatch.setattr(scheduler, "parse_invoice", lambda filename, payload: parsed)
    monkeypatch.setattr(scheduler.FileManager, "save_invoice", AsyncMock(return_value="saved.pdf"))
    monkeypatch.setattr(scheduler.httpx.AsyncClient, "post", post_mock, raising=False)

    await scheduler.scan_all_accounts()

    webhook_logs = (await db.execute(select(WebhookLog))).scalars().all()
    assert webhook_logs == []
    post_mock.assert_not_awaited()
