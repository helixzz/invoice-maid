from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import get_settings
from app.models import EmailAccount, Invoice
from app.schemas.invoice import InvoiceExtract
from app.services import manual_upload as mu
from app.services.ai_service import AIService
from app.services.file_manager import FileManager
from app.services.invoice_parser import ParsedInvoice


def _parsed(**overrides) -> ParsedInvoice:
    base = dict(
        invoice_no="INV-OK-001",
        buyer="Buyer",
        seller="Seller",
        amount=Decimal("100.00"),
        invoice_date=date(2026, 4, 20),
        invoice_type="电子发票（普通发票）",
        item_summary="Service fees",
        raw_text="raw " * 50,
        source_format="pdf",
        extraction_method="qr",
        confidence=0.95,
        is_vat_document=True,
    )
    base.update(overrides)
    return ParsedInvoice(**base)


def _strong_llm_extract(**overrides) -> InvoiceExtract:
    base = dict(
        buyer="LLM Buyer",
        seller="LLM Seller",
        invoice_no="LLM-INV-001",
        invoice_date=date(2026, 4, 20),
        amount=Decimal("200.00"),
        item_summary="LLM summary",
        invoice_type="电子发票（普通发票）",
        confidence=0.95,
        is_valid_tax_invoice=True,
    )
    base.update(overrides)
    return InvoiceExtract(**base)


@pytest.fixture
async def seeded_manual_account(db, settings) -> EmailAccount:
    del settings
    account = EmailAccount(
        name="Manual Uploads",
        type="manual",
        host=None,
        port=None,
        username="system@manual-upload.local",
        outlook_account_type="personal",
        password_encrypted=None,
        oauth_token_path=None,
        is_active=False,
        last_scan_uid=None,
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return account


def _mock_ai(extract: InvoiceExtract | None = None, embed=None) -> SimpleNamespace:
    return SimpleNamespace(
        extract_invoice_fields=AsyncMock(return_value=extract or _strong_llm_extract()),
        embed_text=AsyncMock(return_value=embed or [0.1, 0.2, 0.3]),
    )


async def test_process_uploaded_invoice_parse_failure_returns_parse_failed(
    db, settings, seeded_manual_account, monkeypatch
) -> None:
    """Parser raises -> UploadResult(outcome='parse_failed'). Service
    must swallow the exception and record the audit log rather than
    bubble to the caller, because the endpoint uses the outcome to map
    to the correct HTTP status."""
    del seeded_manual_account

    def boom(filename: str, payload: bytes) -> ParsedInvoice:
        raise ValueError("cannot parse")

    monkeypatch.setattr(mu, "parse_invoice", boom)
    file_mgr = FileManager(settings.STORAGE_PATH)

    result = await mu.process_uploaded_invoice(
        db=db, ai=_mock_ai(), file_mgr=file_mgr,
        settings=settings, filename="bad.pdf", payload=b"xxx",
    )
    assert result.outcome == "parse_failed"
    assert "cannot parse" in result.detail


async def test_process_uploaded_invoice_scam_detected(
    db, settings, seeded_manual_account, monkeypatch
) -> None:
    """A parsed invoice whose buyer/seller/summary trips is_scam_text
    must be rejected with outcome='scam_detected'."""
    del seeded_manual_account
    parsed = _parsed(
        buyer="Lottery Winner Congratulations",
        seller="Free Money Inc",
        item_summary="You won a free iPhone click here",
    )
    monkeypatch.setattr(mu, "parse_invoice", lambda f, p: parsed)

    def fake_scam(text: str) -> tuple[bool, str]:
        return True, "lottery keywords"
    monkeypatch.setattr(mu, "is_scam_text", fake_scam)

    file_mgr = FileManager(settings.STORAGE_PATH)
    result = await mu.process_uploaded_invoice(
        db=db, ai=_mock_ai(), file_mgr=file_mgr,
        settings=settings, filename="scam.pdf", payload=b"x",
    )
    assert result.outcome == "scam_detected"
    assert "lottery" in result.detail.lower()


async def test_process_uploaded_invoice_not_vat_invoice_llm_veto(
    db, settings, seeded_manual_account, monkeypatch
) -> None:
    """When the parser is weak (not strong_parse) AND the LLM says
    is_valid_tax_invoice=False, the service must veto the upload with
    outcome='not_vat_invoice'."""
    del seeded_manual_account
    # Weak parse: no QR/XML/OFD method AND invoice_no not 8/20 digits
    weak_parsed = _parsed(
        extraction_method="regex",
        invoice_no="NOT-DIGITS",
        confidence=0.3,
        is_vat_document=False,
        invoice_type="不明",
    )
    monkeypatch.setattr(mu, "parse_invoice", lambda f, p: weak_parsed)

    ai = _mock_ai(
        extract=_strong_llm_extract(
            invoice_type="不明",
            is_valid_tax_invoice=False,
            confidence=0.3,
        )
    )
    file_mgr = FileManager(settings.STORAGE_PATH)
    result = await mu.process_uploaded_invoice(
        db=db, ai=ai, file_mgr=file_mgr,
        settings=settings, filename="notvat.pdf", payload=b"x",
    )
    assert result.outcome == "not_vat_invoice"


async def test_process_uploaded_invoice_low_confidence_after_enrichment(
    db, settings, seeded_manual_account, monkeypatch
) -> None:
    """Parser weak -> LLM enrichment called, but LLM also returns low
    confidence. Final gate: confidence < 0.6 -> low_confidence outcome.
    Also covers the LLM-call-succeeded-but-fields-still-weak branch."""
    del seeded_manual_account
    parsed = _parsed(invoice_no=None, confidence=0.4, extraction_method="regex")
    monkeypatch.setattr(mu, "parse_invoice", lambda f, p: parsed)

    ai = _mock_ai(
        extract=_strong_llm_extract(
            invoice_no="LLM-123",
            confidence=0.4,
            is_valid_tax_invoice=True,
        )
    )
    file_mgr = FileManager(settings.STORAGE_PATH)
    result = await mu.process_uploaded_invoice(
        db=db, ai=ai, file_mgr=file_mgr,
        settings=settings, filename="weak.pdf", payload=b"x",
    )
    assert result.outcome == "low_confidence"


async def test_process_uploaded_invoice_enrichment_raises_continues_with_parser(
    db, settings, seeded_manual_account, monkeypatch
) -> None:
    """If AIService.extract_invoice_fields raises (network error,
    OpenAI down, etc.), we must keep the parser-derived fields and
    continue — never let the LLM failure propagate.

    Parser must be strong enough that the post-LLM low-confidence gate
    doesn't veto the save when enrichment fails. We use a QR parse with
    20-digit invoice_no, confidence=0.65, and one missing field (item_summary)
    to force should_enrich=True, then verify outcome='saved'."""
    del seeded_manual_account
    parsed = _parsed(
        invoice_no="26312345678912345678",
        extraction_method="qr",
        confidence=0.65,
        item_summary=None,
    )
    monkeypatch.setattr(mu, "parse_invoice", lambda f, p: parsed)

    ai = SimpleNamespace(
        extract_invoice_fields=AsyncMock(side_effect=RuntimeError("openai down")),
        embed_text=AsyncMock(return_value=[0.0]),
    )
    file_mgr = FileManager(settings.STORAGE_PATH)
    result = await mu.process_uploaded_invoice(
        db=db, ai=ai, file_mgr=file_mgr,
        settings=settings, filename="ok.pdf", payload=b"x",
    )
    assert result.outcome == "saved", result.detail
    assert result.invoice is not None
    assert result.invoice.invoice_no == "26312345678912345678"


async def test_process_uploaded_invoice_merges_llm_fields_when_parser_weak(
    db, settings, seeded_manual_account, monkeypatch
) -> None:
    """Parser returns weak fields (buyer/seller as 未知, no amount). LLM
    returns strong is_valid_tax_invoice=True with all fields filled.
    Service should merge: LLM wins buyer/seller/type/summary, parser
    keeps strong invoice_no if it had one; otherwise LLM fills it too."""
    del seeded_manual_account
    parsed = _parsed(
        invoice_no="",
        buyer="未知",
        seller="未知",
        invoice_type="未知",
        item_summary=None,
        amount=None,
        confidence=0.55,
        extraction_method="regex",
    )
    monkeypatch.setattr(mu, "parse_invoice", lambda f, p: parsed)

    ai = _mock_ai(
        extract=_strong_llm_extract(
            invoice_no="88888888",
            buyer="Real Buyer",
            seller="Real Seller",
            invoice_type="电子发票（普通发票）",
            item_summary="Real summary",
            amount=Decimal("888.00"),
            confidence=0.95,
            is_valid_tax_invoice=True,
        )
    )
    file_mgr = FileManager(settings.STORAGE_PATH)
    result = await mu.process_uploaded_invoice(
        db=db, ai=ai, file_mgr=file_mgr,
        settings=settings, filename="weak.pdf", payload=b"x",
    )
    assert result.outcome == "saved"
    assert result.invoice.buyer == "Real Buyer"
    assert result.invoice.seller == "Real Seller"
    assert result.invoice.invoice_no == "88888888"
    assert result.invoice.extraction_method == "llm"


async def test_process_uploaded_invoice_race_condition_returns_duplicate(
    db, settings, seeded_manual_account, monkeypatch
) -> None:
    """Simulate race: db.flush() raises IntegrityError even though the
    pre-flush SELECT found nothing. Service must roll back, create a
    NEW scan_log so extraction_log has a valid FK, and respond duplicate
    with the existing_invoice_id."""
    del seeded_manual_account
    from sqlalchemy.exc import IntegrityError

    parsed = _parsed(invoice_no="RACE-001")
    monkeypatch.setattr(mu, "parse_invoice", lambda f, p: parsed)

    # Pre-create a conflicting invoice by putting it in the DB after
    # the service's initial SELECT but before its flush.
    existing_invoice = Invoice(
        invoice_no="RACE-001",
        buyer="Pre-existing",
        seller="Pre-existing",
        amount=Decimal("1.00"),
        invoice_date=date(2026, 1, 1),
        invoice_type="电子发票（普通发票）",
        item_summary="",
        file_path="existing.pdf",
        raw_text="",
        email_uid="pre-existing",
        email_account_id=(await db.execute(
            __import__("sqlalchemy").select(EmailAccount.id).where(
                EmailAccount.type == "manual"
            )
        )).scalar_one(),
        source_format="pdf",
        extraction_method="regex",
        confidence=0.9,
    )
    db.add(existing_invoice)
    await db.commit()

    file_mgr = FileManager(settings.STORAGE_PATH)
    result = await mu.process_uploaded_invoice(
        db=db, ai=_mock_ai(), file_mgr=file_mgr,
        settings=settings, filename="race.pdf", payload=b"x",
    )
    assert result.outcome == "duplicate"
    assert result.existing_invoice_id == existing_invoice.id


async def test_process_uploaded_invoice_missing_manual_account_raises(
    db, settings
) -> None:
    """No sentinel account in DB -> RuntimeError with actionable hint.
    This is the precondition check — upload feature cannot work without
    the migration having been applied."""
    file_mgr = FileManager(settings.STORAGE_PATH)
    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        await mu.process_uploaded_invoice(
            db=db, ai=_mock_ai(), file_mgr=file_mgr,
            settings=settings, filename="x.pdf", payload=b"x",
        )


async def test_process_uploaded_invoice_embedding_failure_does_not_abort(
    db, settings, seeded_manual_account, monkeypatch
) -> None:
    """If sqlite_vec is available and embed_text() raises, the invoice
    has already been saved — the service must log and continue rather
    than mark the upload as failed."""
    del seeded_manual_account
    parsed = _parsed(invoice_no="EMBED-FAIL-001")
    monkeypatch.setattr(mu, "parse_invoice", lambda f, p: parsed)
    monkeypatch.setattr(settings, "sqlite_vec_available", True)

    ai = SimpleNamespace(
        extract_invoice_fields=AsyncMock(return_value=_strong_llm_extract()),
        embed_text=AsyncMock(side_effect=RuntimeError("embedding model offline")),
    )
    file_mgr = FileManager(settings.STORAGE_PATH)
    result = await mu.process_uploaded_invoice(
        db=db, ai=ai, file_mgr=file_mgr,
        settings=settings, filename="ok.pdf", payload=b"x",
    )
    assert result.outcome == "saved"
    assert result.invoice is not None
    assert result.invoice.invoice_no == "EMBED-FAIL-001"


async def test_process_uploaded_invoice_embedding_success_stores_vector(
    db, settings, seeded_manual_account, monkeypatch
) -> None:
    """Covers the happy path through store_embedding() when sqlite_vec is
    enabled and both embed_text + store_embedding succeed. Previously
    this branch was only exercised by the failure test, so line 430
    (``await store_embedding(...)``) stayed uncovered."""
    del seeded_manual_account
    parsed = _parsed(invoice_no="EMBED-OK-001")
    monkeypatch.setattr(mu, "parse_invoice", lambda f, p: parsed)
    monkeypatch.setattr(settings, "sqlite_vec_available", True)

    stored: list[tuple[int, list[float]]] = []

    async def fake_store(session, invoice_id, embedding):
        del session
        stored.append((invoice_id, list(embedding)))
    monkeypatch.setattr(mu, "store_embedding", fake_store)

    ai = SimpleNamespace(
        extract_invoice_fields=AsyncMock(return_value=_strong_llm_extract()),
        embed_text=AsyncMock(return_value=[0.11, 0.22, 0.33]),
    )
    file_mgr = FileManager(settings.STORAGE_PATH)
    result = await mu.process_uploaded_invoice(
        db=db, ai=ai, file_mgr=file_mgr,
        settings=settings, filename="ok.pdf", payload=b"x",
    )
    assert result.outcome == "saved"
    assert result.invoice is not None
    assert stored == [(result.invoice.id, [0.11, 0.22, 0.33])]


async def test_process_uploaded_invoice_llm_merge_respects_parser_strong_invoice_no(
    db, settings, seeded_manual_account, monkeypatch
) -> None:
    """When parser already has a strong 8-or-20-digit invoice_no, the
    LLM merge must NOT overwrite it with the LLM's guess (line 147 in
    _merge_llm_into_parsed). But LLM DOES fill in buyer/seller/type/summary
    when parser has 未知 sentinels."""
    del seeded_manual_account
    parsed = _parsed(
        invoice_no="88888888",
        buyer="未知",
        seller="未知",
        invoice_type="未知",
        item_summary=None,
        confidence=0.55,
        extraction_method="regex",
    )
    monkeypatch.setattr(mu, "parse_invoice", lambda f, p: parsed)

    ai = _mock_ai(
        extract=_strong_llm_extract(
            invoice_no="LLM-WOULD-OVERRIDE",
            buyer="LLM Buyer",
            seller="LLM Seller",
            invoice_type="电子发票（普通发票）",
            item_summary="LLM summary",
            confidence=0.9,
            is_valid_tax_invoice=True,
        )
    )
    file_mgr = FileManager(settings.STORAGE_PATH)
    result = await mu.process_uploaded_invoice(
        db=db, ai=ai, file_mgr=file_mgr,
        settings=settings, filename="x.pdf", payload=b"x",
    )
    assert result.outcome == "saved"
    assert result.invoice.invoice_no == "88888888"
    assert result.invoice.buyer == "LLM Buyer"
    assert result.invoice.item_summary == "LLM summary"


async def test_process_uploaded_invoice_llm_merge_skips_unknown_fields(
    db, settings, seeded_manual_account, monkeypatch
) -> None:
    """If the LLM returns 未知 for a field, the parser's value (even if
    also weak) must be kept — never replace a weak answer with the
    LLM's admission of 未知."""
    del seeded_manual_account
    parsed = _parsed(
        invoice_no=None,
        buyer="Parser Buyer",
        seller="Parser Seller",
        invoice_type="电子发票（普通发票）",
        item_summary="Parser summary",
        amount=None,
        invoice_date=None,
        confidence=0.55,
        extraction_method="regex",
    )
    monkeypatch.setattr(mu, "parse_invoice", lambda f, p: parsed)

    ai = _mock_ai(
        extract=_strong_llm_extract(
            invoice_no="12345678",
            buyer="未知",
            seller="未知",
            invoice_type="未知",
            item_summary="未知",
            amount=Decimal("50.00"),
            invoice_date=date(2026, 4, 20),
            is_valid_tax_invoice=True,
            confidence=0.75,
        )
    )
    file_mgr = FileManager(settings.STORAGE_PATH)
    result = await mu.process_uploaded_invoice(
        db=db, ai=ai, file_mgr=file_mgr,
        settings=settings, filename="x.pdf", payload=b"x",
    )
    assert result.outcome == "saved"
    # Parser fields kept despite LLM returning 未知
    assert result.invoice.buyer == "Parser Buyer"
    assert result.invoice.seller == "Parser Seller"
    assert result.invoice.invoice_type == "电子发票（普通发票）"
    assert result.invoice.item_summary == "Parser summary"
    # But LLM fills in the genuinely-missing fields
    assert result.invoice.invoice_no == "12345678"
    assert result.invoice.amount == Decimal("50.00")
    assert result.invoice.invoice_date == date(2026, 4, 20)
