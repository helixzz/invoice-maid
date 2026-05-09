from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.services.invoice_csv import (
    CSV_COLUMNS,
    CSV_UTF8_BOM,
    SUMMARY_FILENAME,
    build_csv_bytes,
    build_csv_content,
    invoice_csv_row,
)


def _make_invoice(**overrides):
    defaults = {
        "invoice_no": "INV-001",
        "buyer": "Acme Corp",
        "seller": "Widget Co",
        "amount": Decimal("1234.56"),
        "invoice_date": date(2026, 4, 20),
        "invoice_category": "vat_invoice",
        "invoice_type": "电子发票",
        "item_summary": "widgets",
        "extraction_method": "llm",
        "confidence": 0.93,
        "created_at": datetime(2026, 4, 20, 10, 30, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_summary_filename_constant_matches_zip_arcname() -> None:
    assert SUMMARY_FILENAME == "invoices_summary.csv"


def test_csv_columns_has_expected_order() -> None:
    assert CSV_COLUMNS == [
        "invoice_no",
        "buyer",
        "seller",
        "amount",
        "invoice_date",
        "invoice_category",
        "invoice_type",
        "item_summary",
        "extraction_method",
        "confidence",
        "created_at",
    ]


def test_invoice_csv_row_renders_in_column_order() -> None:
    row = invoice_csv_row(_make_invoice())
    assert row == [
        "INV-001",
        "Acme Corp",
        "Widget Co",
        "1234.56",
        "2026-04-20",
        "vat_invoice",
        "电子发票",
        "widgets",
        "llm",
        0.93,
        "2026-04-20T10:30:00+00:00",
    ]


def test_invoice_csv_row_empty_item_summary_renders_as_blank() -> None:
    row = invoice_csv_row(_make_invoice(item_summary=None))
    assert row[7] == ""


def test_build_csv_content_emits_header_then_rows() -> None:
    invoices = [_make_invoice(invoice_no="INV-001"), _make_invoice(invoice_no="INV-002")]
    content = build_csv_content(invoices)

    lines = content.strip().split("\n")
    assert lines[0].strip() == ",".join(CSV_COLUMNS)
    assert "INV-001" in lines[1]
    assert "INV-002" in lines[2]


def test_build_csv_content_empty_list_still_has_header() -> None:
    content = build_csv_content([])
    assert content.strip() == ",".join(CSV_COLUMNS)


def test_build_csv_bytes_prepends_utf8_bom() -> None:
    out = build_csv_bytes([_make_invoice()])
    assert out.startswith(CSV_UTF8_BOM)
    assert CSV_UTF8_BOM == b"\xef\xbb\xbf"


def test_build_csv_bytes_chinese_characters_survive_roundtrip() -> None:
    """Chinese seller names must render correctly in Excel / WPS / Numbers.
    The UTF-8 BOM is what signals to those tools that the file is UTF-8
    rather than GB2312 / GBK — without it, Chinese breaks on Excel for
    Windows, which is the single biggest real-world consumer of this CSV."""
    invoices = [
        _make_invoice(seller="戴鑫技术有限公司", buyer="上海某某贸易"),
    ]
    out = build_csv_bytes(invoices)
    assert out.startswith(CSV_UTF8_BOM)
    decoded = out[len(CSV_UTF8_BOM):].decode("utf-8")
    assert "戴鑫技术有限公司" in decoded
    assert "上海某某贸易" in decoded


def test_build_csv_bytes_comma_in_buyer_is_properly_quoted() -> None:
    invoices = [_make_invoice(buyer="Acme, Inc.", seller="has\nnewline too")]
    out = build_csv_bytes(invoices)
    decoded = out[len(CSV_UTF8_BOM):].decode("utf-8")
    assert '"Acme, Inc."' in decoded
    assert '"has\nnewline too"' in decoded
