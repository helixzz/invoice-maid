"""Zero-dependency OOXML (.xlsx) workbook generator for reimbursement reports.

Produces a valid Excel workbook with two worksheets:
  1. ``Summary`` — totals grouped by currency and by reimbursement status
  2. ``Invoices`` — full structured invoice rows including currency, status, tags, notes
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import io
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape
import zipfile

if TYPE_CHECKING:  # pragma: no cover
    from app.models import Invoice

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
REIMBURSEMENT_XLSX_FILENAME = "reimbursement_summary.xlsx"

_DETAIL_HEADERS = [
    "发票号码",
    "开票日期",
    "销售方",
    "购买方",
    "金额",
    "币种",
    "分类",
    "发票类型",
    "报销状态",
    "标签",
    "备注",
    "项目摘要",
]


def _col_letter(idx: int) -> str:
    letters = ""
    n = idx + 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _render_sheet_xml(rows: list[list[str | int | float | Decimal]]) -> str:
    row_xml: list[str] = []
    for r_idx, row in enumerate(rows, start=1):
        cells: list[str] = []
        for c_idx, val in enumerate(row):
            ref = f"{_col_letter(c_idx)}{r_idx}"
            if isinstance(val, (int, float, Decimal)) and not isinstance(val, bool):
                cells.append(f'<c r="{ref}"><v>{val}</v></c>')
            else:
                text = escape(str(val or ""))
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>')
        row_xml.append(f'<row r="{r_idx}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(row_xml)}</sheetData>'
        "</worksheet>"
    )


def build_xlsx_bytes(invoices: list["Invoice"]) -> bytes:
    """Render ``invoices`` into an Office Open XML (.xlsx) byte payload."""
    by_currency: dict[str, tuple[int, Decimal]] = defaultdict(lambda: (0, Decimal("0")))
    by_status: dict[str, int] = defaultdict(int)

    detail_rows: list[list[str | int | float | Decimal]] = [list(_DETAIL_HEADERS)]
    for inv in invoices:
        cur = getattr(inv, "currency", None) or "CNY"
        status = getattr(inv, "reimbursement_status", None) or "unreimbursed"
        amt = Decimal(str(inv.amount or 0))
        cnt, total = by_currency[cur]
        by_currency[cur] = (cnt + 1, total + amt)
        by_status[status] += 1

        detail_rows.append(
            [
                inv.invoice_no,
                inv.invoice_date.isoformat(),
                inv.seller,
                inv.buyer,
                float(amt),
                cur,
                inv.invoice_category,
                inv.invoice_type,
                status,
                getattr(inv, "tags", None) or "",
                getattr(inv, "notes", None) or "",
                inv.item_summary or "",
            ]
        )

    summary_rows: list[list[str | int | float | Decimal]] = [
        ["按币种汇总 (By Currency)", "发票张数", "合计金额"],
    ]
    for cur in sorted(by_currency):
        cnt, total = by_currency[cur]
        summary_rows.append([cur, cnt, float(total)])

    summary_rows.append([])
    summary_rows.append(["按报销状态汇总 (By Reimbursement Status)", "发票张数"])
    for st in sorted(by_status):
        summary_rows.append([st, by_status[st]])

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<sheets>"
        '<sheet name="Summary" sheetId="1" r:id="rId1"/>'
        '<sheet name="Invoices" sheetId="2" r:id="rId2"/>'
        "</sheets>"
        "</workbook>"
    )
    wb_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>'
        "</Relationships>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        zf.writestr("xl/worksheets/sheet1.xml", _render_sheet_xml(summary_rows))
        zf.writestr("xl/worksheets/sheet2.xml", _render_sheet_xml(detail_rows))
    return buf.getvalue()
