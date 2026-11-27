from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

import app.tasks.scheduler as scheduler_mod
from app.models import EmailAccount, ExtractionLog, FailedEmail
from app.schemas.invoice import InvoiceCategory
from app.services.ai_service import LLMCircuitOpenError, LLMUnavailableError
from app.services.email_scanner import RawAttachment, RawEmail
from app.services.failed_email_queue import enqueue_failed_email
from app.services.invoice_parser import ParsedInvoice


def _make_email(uid: str = "u-1", **overrides) -> RawEmail:
    defaults = {
        "uid": uid,
        "subject": "Test subject",
        "body_text": "body text",
        "body_html": "",
        "from_addr": "sender@example.com",
        "received_at": datetime.now(timezone.utc),
        "attachments": [],
        "body_links": ["https://example.com/info"],
        "headers": {},
        "is_hydrated": True,
    }
    defaults.update(overrides)
    return RawEmail(**defaults)


@pytest.mark.asyncio
async def test_preflight_skips_hydration_and_resolves_retry_entry(
    db, settings, create_email_account, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = await create_email_account(type="outlook")
    noise_email = _make_email(
        uid="gh-1",
        from_addr="GitHub <notifications@github.com>",
        subject="[repo] PR run failed: CI",
        body_text="check logs",
        is_hydrated=False,
    )
    await enqueue_failed_email(
        db,
        user_id=account.user_id,
        email_account_id=account.id,
        email_data=noise_email,
        error_message="earlier error",
    )
    row = (await db.execute(select(FailedEmail).where(FailedEmail.email_uid == "gh-1"))).scalar_one()
    row.next_retry_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db.commit()

    hydrate_mock = AsyncMock(side_effect=AssertionError("should not hydrate blocked noise email"))
    fake_scanner = SimpleNamespace(
        scan=AsyncMock(return_value=[]),
        hydrate_email=hydrate_mock,
        _last_scan_state=None,
    )
    monkeypatch.setattr(scheduler_mod.ScannerFactory, "get_scanner", lambda _: fake_scanner)
    monkeypatch.setattr(scheduler_mod, "get_db", lambda: _fresh_session(db))

    await scheduler_mod.scan_all_accounts(account_id=account.id)
    hydrate_mock.assert_not_awaited()

    await db.refresh(row)
    assert row.status == "resolved"
    logs = (await db.execute(select(ExtractionLog).where(ExtractionLog.email_uid == "gh-1"))).scalars().all()
    assert len(logs) == 1
    assert logs[0].outcome == "not_invoice"

    # Also test preflight rejection on a non-retry-queue email (from_retry_queue=False branch)
    noise2 = _make_email(
        uid="gh-2",
        from_addr="GitHub <notifications@github.com>",
        subject="[repo] PR run failed: CI 2",
        body_text="logs",
        is_hydrated=False,
    )
    fake_scanner_2 = SimpleNamespace(
        scan=AsyncMock(return_value=[noise2]),
        hydrate_email=hydrate_mock,
        _last_scan_state=None,
    )
    monkeypatch.setattr(scheduler_mod.ScannerFactory, "get_scanner", lambda _: fake_scanner_2)
    await scheduler_mod.scan_all_accounts(account_id=account.id)
    hydrate_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_hydrate_error_enqueues_retry_and_writes_extraction_log(
    db, settings, create_email_account, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = await create_email_account(type="outlook")
    raw = _make_email(uid="hyd-1", is_hydrated=False, headers={"_graph_id": "g1"})

    async def failing_hydrate(_acc, mail):
        mail.headers["_hydrate_error"] = "429 Too Many Requests"
        mail.is_hydrated = False
        return mail

    fake_scanner = SimpleNamespace(
        scan=AsyncMock(return_value=[raw]),
        hydrate_email=failing_hydrate,
        _last_scan_state="state-after",
    )
    monkeypatch.setattr(scheduler_mod.ScannerFactory, "get_scanner", lambda _: fake_scanner)
    monkeypatch.setattr(scheduler_mod, "get_db", lambda: _fresh_session(db))

    await scheduler_mod.scan_all_accounts(account_id=account.id)

    queued = (await db.execute(select(FailedEmail).where(FailedEmail.email_uid == "hyd-1"))).scalar_one()
    assert queued.status == "pending"
    assert "429" in queued.error_message
    logs = (await db.execute(select(ExtractionLog).where(ExtractionLog.email_uid == "hyd-1"))).scalars().all()
    assert logs[0].outcome == "error"


@pytest.mark.asyncio
async def test_llm_outage_enqueues_failed_email_and_resolves_on_later_success(
    db, settings, create_email_account, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = await create_email_account()
    raw = _make_email(uid="llm-1", subject="您的京东订单电子发票已开具", body_text="请查收", body_links=["https://jd.com/inv.pdf"])

    class FailingAI:
        def __init__(self, _settings):
            pass

        async def analyze_email(self, *args, **kwargs):
            raise LLMCircuitOpenError("circuit open")

    fake_scanner = SimpleNamespace(scan=AsyncMock(return_value=[raw]), _last_scan_state="new-wm")
    monkeypatch.setattr(scheduler_mod.ScannerFactory, "get_scanner", lambda _: fake_scanner)
    monkeypatch.setattr(scheduler_mod, "AIService", FailingAI)
    monkeypatch.setattr(scheduler_mod, "get_db", lambda: _fresh_session(db))

    await scheduler_mod.scan_all_accounts(account_id=account.id)

    queued = (await db.execute(select(FailedEmail).where(FailedEmail.email_uid == "llm-1"))).scalar_one()
    assert queued.status == "pending"
    logs = (await db.execute(select(ExtractionLog).where(ExtractionLog.email_uid == "llm-1"))).scalars().all()
    assert logs[0].outcome == "error"

    # Now make the retry due and let AI classify as non-invoice -> resolves cleanly
    queued.next_retry_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db.commit()

    class RecoveringAI:
        def __init__(self, _settings):
            pass

        async def analyze_email(self, *args, **kwargs):
            return SimpleNamespace(is_invoice_related=False, should_download=False)

    fake_scanner2 = SimpleNamespace(scan=AsyncMock(return_value=[]), _last_scan_state=None)
    monkeypatch.setattr(scheduler_mod.ScannerFactory, "get_scanner", lambda _: fake_scanner2)
    monkeypatch.setattr(scheduler_mod, "AIService", RecoveringAI)

    await scheduler_mod.scan_all_accounts(account_id=account.id)
    await db.refresh(queued)
    assert queued.status == "resolved"


@pytest.mark.asyncio
async def test_retry_queue_resolution_on_invoice_save_and_watermark_hold_on_unqueued_system_error(
    db, settings, create_email_account, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = await create_email_account(last_scan_uid="old-wm")
    # 1) Unqueued system error holds watermark
    monkeypatch.setattr(
        scheduler_mod,
        "_process_single_email",
        AsyncMock(
            return_value=scheduler_mod._EmailResult(
                last_uid="u-fail",
                error="llm down",
                system_error=True,
                enqueued_retry=False,
            )
        ),
    )
    fake_scanner = SimpleNamespace(
        scan=AsyncMock(return_value=[_make_email(uid="u-fail")]),
        _last_scan_state="should-not-advance",
    )
    monkeypatch.setattr(scheduler_mod.ScannerFactory, "get_scanner", lambda _: fake_scanner)
    monkeypatch.setattr(scheduler_mod, "get_db", lambda: _fresh_session(db))

    await scheduler_mod.scan_all_accounts(account_id=account.id)
    await db.refresh(account)
    assert account.last_scan_uid == "old-wm"


@pytest.mark.asyncio
async def test_retry_queue_resolves_after_full_invoice_pipeline_with_empty_body(
    db, settings, create_email_account, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = await create_email_account()
    raw = _make_email(
        uid="retry-save-1",
        subject="Invoice attached",
        body_text="",
        body_links=[],
        attachments=[RawAttachment(filename="inv.pdf", payload=b"%PDF-1.4 fake", content_type="application/pdf")],
        headers={"_from_retry_queue": "True"},
    )
    await enqueue_failed_email(
        db,
        user_id=account.user_id,
        email_account_id=account.id,
        email_data=raw,
        error_message="earlier",
    )
    entry = (await db.execute(select(FailedEmail).where(FailedEmail.email_uid == "retry-save-1"))).scalar_one()

    parsed = ParsedInvoice(
        invoice_no="20261127000012345678",
        buyer="",
        seller="",
        amount=Decimal("88.00"),
        invoice_date=date(2026, 11, 27),
        invoice_type="增值税电子普通发票",
        item_summary="",
        raw_text="发票号码: 20261127000012345678",
        source_format="pdf",
        extraction_method="regex",
        confidence=0.5,
        is_vat_document=True,
    )
    extracted = SimpleNamespace(
        is_valid_tax_invoice=True,
        invoice_category=InvoiceCategory.VAT_INVOICE,
        invoice_no="20261127000012345678",
        buyer="Buyer Co",
        seller="Seller Co",
        amount=88.0,
        invoice_date="2026-11-27",
        invoice_type="增值税电子普通发票",
        item_summary="Service",
        confidence=0.95,
    )

    class OkAI:
        async def extract_invoice_fields(self, _db, text: str):
            assert "[Email context]" not in text
            return extracted

    monkeypatch.setattr(scheduler_mod, "parse_invoice", lambda _fn, _data: parsed)
    monkeypatch.setattr(scheduler_mod, "get_db", lambda: _fresh_session(db))

    file_mgr = scheduler_mod.FileManager(settings.STORAGE_PATH)
    res = await scheduler_mod._process_single_email(
        email_data=raw,
        classifier=scheduler_mod.EmailClassifier(),
        ai=OkAI(),
        file_mgr=file_mgr,
        settings=settings,
        log_id=1,
        account_id=account.id,
        user_id=account.user_id,
    )
    assert res.invoices_added == 1
    await db.refresh(entry)
    assert entry.status == "resolved"


@pytest.mark.asyncio
async def test_non_system_email_error_logs_without_enqueuing_retry(
    db, settings, create_email_account, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = await create_email_account()
    raw = _make_email(uid="non-sys-1", subject="Weird", body_text="abc", body_links=["https://x.com/1"])

    class CrashClassifier:
        def classify_tier1(self, _mail):
            raise ValueError("unexpected parse error")

    monkeypatch.setattr(scheduler_mod, "get_db", lambda: _fresh_session(db))
    res = await scheduler_mod._process_single_email(
        email_data=raw,
        classifier=CrashClassifier(),
        ai=scheduler_mod.AIService(settings),
        file_mgr=scheduler_mod.FileManager(settings.STORAGE_PATH),
        settings=settings,
        log_id=1,
        account_id=account.id,
        user_id=account.user_id,
    )
    assert res.error == "unexpected parse error"
    assert res.system_error is False
    assert res.enqueued_retry is False
    queued = (await db.execute(select(FailedEmail).where(FailedEmail.email_uid == "non-sys-1"))).scalar_one_or_none()
    assert queued is None


@pytest.mark.asyncio
async def test_clean_extracted_url_repairs_jd_presigned_links_and_logs_download_failed(
    db, settings, create_email_account, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.email_scanner import _clean_extracted_url, _extract_urls

    raw_html = (
        '<a href="https://eicore-invoice-26.s3.cn-north-1.jdcloud-oss.com/digital.pdf'
        '?AWSAccessKeyId=AK123&amp;Expires=2742515364&amp;Signature=abc%3D">PDF</a> '
        'https://eicore-invoice-26.s3.cn-north-1.jdcloud-oss.com/digital2.pdf'
        '?AWSAccessKeyId=AK123;Expires=2742515364&Signature=def%3D '
        'https://plain.example.com/no-query'
    )
    urls = _extract_urls(raw_html)
    assert urls[0].endswith("?AWSAccessKeyId=AK123&Expires=2742515364&Signature=abc%3D")
    assert urls[1].endswith("?AWSAccessKeyId=AK123&Expires=2742515364&Signature=def%3D")
    assert _clean_extracted_url("https://plain.example.com/no-query") == "https://plain.example.com/no-query"

    # Failed download with no fallback attachments records download_failed ExtractionLog
    account = await create_email_account()
    raw = _make_email(
        uid="dl-fail-1",
        subject="您的京东订单电子发票已开具",
        body_text="请点击下载",
        body_links=["https://example.com/inv.pdf"],
        attachments=[],
    )

    class LinkAI:
        async def analyze_email(self, *args, **kwargs):
            return SimpleNamespace(
                is_invoice_related=True,
                should_download=True,
                best_download_url="https://example.com/inv.pdf",
                url_is_safelink=False,
                extraction_hints=SimpleNamespace(likely_formats=[]),
            )

    monkeypatch.setattr(scheduler_mod, "_download_linked_invoice", AsyncMock(return_value=None))
    monkeypatch.setattr(scheduler_mod, "get_db", lambda: _fresh_session(db))

    res = await scheduler_mod._process_single_email(
        email_data=raw,
        classifier=scheduler_mod.EmailClassifier(),
        ai=LinkAI(),
        file_mgr=scheduler_mod.FileManager(settings.STORAGE_PATH),
        settings=settings,
        log_id=1,
        account_id=account.id,
        user_id=account.user_id,
    )
    assert res.invoices_added == 0
    logs = (await db.execute(select(ExtractionLog).where(ExtractionLog.email_uid == "dl-fail-1"))).scalars().all()
    assert len(logs) == 1
    assert logs[0].outcome == "download_failed"
    assert logs[0].download_outcome == "failed"

    # When fallback attachment already exists, failed link download does not emit download_failed
    raw_with_att = _make_email(
        uid="dl-fail-with-att",
        subject="Invoice with attachment and broken link",
        body_text="text",
        body_links=["https://example.com/inv.pdf"],
        attachments=[RawAttachment(filename="inv.pdf", payload=b"%PDF-1.4", content_type="application/pdf")],
    )
    monkeypatch.setattr(scheduler_mod, "parse_invoice", lambda _fn, _data: ParsedInvoice(confidence=0.0, raw_text=""))
    await scheduler_mod._process_single_email(
        email_data=raw_with_att,
        classifier=scheduler_mod.EmailClassifier(),
        ai=LinkAI(),
        file_mgr=scheduler_mod.FileManager(settings.STORAGE_PATH),
        settings=settings,
        log_id=1,
        account_id=account.id,
        user_id=account.user_id,
    )
    logs2 = (await db.execute(select(ExtractionLog).where(ExtractionLog.email_uid == "dl-fail-with-att"))).scalars().all()
    assert all(l.outcome != "download_failed" for l in logs2)


async def _fresh_session(db):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(db.bind, expire_on_commit=False)
    async with factory() as session:
        yield session
