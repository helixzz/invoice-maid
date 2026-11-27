from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import fitz
import pytest

from app.schemas.invoice import InvoiceCategory, InvoiceExtract
from app.services.invoice_ingestion import (
    enrich_and_validate_invoice,
    resolve_invoice_currency,
)
from app.services.invoice_parser import (
    ParsedInvoice,
    detect_currency,
    normalize_currency,
    render_pdf_pages_to_base64_png,
)


def _blank_pdf_bytes() -> bytes:
    doc = fitz.open()
    doc.new_page(width=200, height=200)
    data = doc.tobytes()
    doc.close()
    return data


def test_currency_normalization_and_detection() -> None:
    assert normalize_currency("rmb") == "CNY"
    assert normalize_currency("eur") == "EUR"
    assert normalize_currency("invalid-long", default="USD") == "USD"
    assert normalize_currency(None) == "CNY"

    assert detect_currency("") == "CNY"
    assert detect_currency("Total: EUR 49.00") == "EUR"
    assert detect_currency("Total €12.50") == "EUR"
    assert detect_currency("Amount £19.99") == "GBP"
    assert detect_currency("HK$ 120.00") == "HKD"
    assert detect_currency("US$ 20.00") == "USD"
    assert detect_currency("价税合计 ￥88.00") == "CNY"
    assert detect_currency("plain text", default="JPY") == "JPY"

    # InvoiceExtract currency validator
    ext = InvoiceExtract(
        buyer="B",
        seller="S",
        invoice_no="12345678",
        invoice_date=date(2026, 11, 27),
        amount=Decimal("10.00"),
        currency="",
        item_summary="Item",
        invoice_type="电子普通发票",
    )
    assert ext.currency == "CNY"


def test_render_pdf_pages_to_base64_png() -> None:
    assert render_pdf_pages_to_base64_png(b"") == []
    assert render_pdf_pages_to_base64_png(b"not-a-pdf") == []

    imgs = render_pdf_pages_to_base64_png(_blank_pdf_bytes(), max_pages=2)
    assert len(imgs) == 1
    assert len(imgs[0]) > 50


@pytest.mark.asyncio
async def test_vlm_fallback_for_image_only_pdf_and_error_handling(db, settings) -> None:
    pdf_bytes = _blank_pdf_bytes()
    parsed = ParsedInvoice(
        raw_text="",
        source_format="pdf",
        extraction_method="llm",
        confidence=0.0,
    )

    class VlmAI:
        async def extract_invoice_fields_from_images(self, _db, images, context_text=""):
            assert len(images) == 1
            assert "Vendor" in context_text
            return SimpleNamespace(
                is_valid_tax_invoice=False,
                invoice_category=InvoiceCategory.OVERSEAS_INVOICE,
                buyer="Acme Corp",
                seller="Cloud EU GmbH",
                invoice_no="EU-2026-009",
                invoice_date=date(2026, 11, 27),
                amount=Decimal("49.90"),
                currency="EUR",
                invoice_type="Cloud EU Invoice (EUR)",
                item_summary="Monthly subscription",
                confidence=0.91,
            )

    verdict = await enrich_and_validate_invoice(
        db=db,
        ai=VlmAI(),
        settings=settings,
        parsed=parsed,
        payload=pdf_bytes,
        filename="scan.pdf",
        email_context="Provider: Vendor EU",
    )
    assert verdict.accepted is True
    assert verdict.final_currency == "EUR"
    assert verdict.final_category == "overseas_invoice"
    assert parsed.extraction_method == "vlm"
    assert parsed.invoice_no == "EU-2026-009"

    # VLM exception falls back cleanly to low_confidence rejection
    parsed2 = ParsedInvoice(raw_text="", source_format="pdf", extraction_method="llm", confidence=0.0)

    class BrokenVlmAI:
        async def extract_invoice_fields_from_images(self, _db, images, context_text=""):
            del images, context_text
            raise RuntimeError("vision error")

    verdict2 = await enrich_and_validate_invoice(
        db=db,
        ai=BrokenVlmAI(),
        settings=settings,
        parsed=parsed2,
        payload=pdf_bytes,
        filename="scan2.pdf",
    )
    assert verdict2.accepted is False

    # Unrenderable PDF bytes skips VLM cleanly
    parsed3 = ParsedInvoice(raw_text="", source_format="pdf", extraction_method="llm", confidence=0.0)
    verdict3 = await enrich_and_validate_invoice(
        db=db,
        ai=VlmAI(),
        settings=settings,
        parsed=parsed3,
        payload=b"corrupt",
        filename="bad.pdf",
    )
    assert verdict3.accepted is False

    # Non-PDF with empty raw_text skips VLM branch cleanly
    parsed4 = ParsedInvoice(raw_text="", source_format="xml", extraction_method="xml_xpath", confidence=0.0)
    verdict4 = await enrich_and_validate_invoice(
        db=db,
        ai=VlmAI(),
        settings=settings,
        parsed=parsed4,
        payload=b"<xml/>",
        filename="empty.xml",
    )
    assert verdict4.accepted is False

    # resolve_invoice_currency fallback when parsed.currency is None
    p_cur = ParsedInvoice(raw_text="Total €10.00")
    assert resolve_invoice_currency(p_cur, final_category="overseas_invoice") == "EUR"
    p_cur_set = ParsedInvoice(currency="GBP")
    assert resolve_invoice_currency(p_cur_set, final_category="overseas_invoice") == "GBP"
