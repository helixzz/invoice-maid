"""Shared helpers for rendering invoices as CSV.

Both the `/invoices/export` endpoint and the `/invoices/batch-download`
endpoint need to render the same CSV layout — the export endpoint
streams the CSV back directly, while batch-download embeds it as
`invoices_summary.csv` inside the ZIP it returns. Keeping both on
a single row builder guarantees the two paths stay in lockstep.
"""

from __future__ import annotations

import csv
from io import StringIO
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from app.models import Invoice


CSV_COLUMNS: list[str] = [
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

CSV_UTF8_BOM: bytes = b"\xef\xbb\xbf"
SUMMARY_FILENAME: str = "invoices_summary.csv"


def invoice_csv_row(invoice: "Invoice") -> list[str | float]:
    return [
        invoice.invoice_no,
        invoice.buyer,
        invoice.seller,
        str(invoice.amount),
        invoice.invoice_date.isoformat(),
        invoice.invoice_category,
        invoice.invoice_type,
        invoice.item_summary or "",
        invoice.extraction_method,
        invoice.confidence,
        invoice.created_at.isoformat(),
    ]


def build_csv_content(invoices: list["Invoice"]) -> str:
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for invoice in invoices:
        writer.writerow(invoice_csv_row(invoice))
    return buffer.getvalue()


def build_csv_bytes(invoices: list["Invoice"]) -> bytes:
    """CSV with UTF-8 BOM so Excel / Numbers / WPS render Chinese correctly."""
    return CSV_UTF8_BOM + build_csv_content(invoices).encode("utf-8")
