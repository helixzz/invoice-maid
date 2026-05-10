from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


OUT = Path(__file__).parent / "invoice_samples"

_NOTO_SC = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
try:
    pdfmetrics.registerFont(TTFont("NotoSansSC", _NOTO_SC))
    CJK_FONT = "NotoSansSC"
except Exception:
    CJK_FONT = "Helvetica"


def make_pdf(path: Path, title: str, body: list[str], font: str = "Helvetica") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path))
    c.setFont(font, 16)
    c.drawString(72, 720, title)
    c.setFont(font, 11)
    y = 690
    for line in body:
        c.drawString(72, y, line)
        y -= 18
    c.save()


def generate_all() -> list[Path]:
    paths: list[Path] = []

    p = OUT / "vat_invoice" / "synthetic_vat.pdf"
    make_pdf(
        p,
        "电子发票（普通发票）",
        [
            "发票号码: 25442000000123456789",
            "开票日期: 2026-04-15",
            "购买方: 测试买方有限公司",
            "  统一社会信用代码: 91110000TEST000001",
            "销售方: 测试销售有限公司",
            "  统一社会信用代码: 91110000TEST000002",
            "商品名称: 办公用品",
            "金额: 100.00",
            "税率: 13%",
            "税额: 13.00",
            "价税合计（大写）: 壹佰壹拾叁圆整",
            "价税合计（小写）: ¥113.00",
        ],
        font=CJK_FONT,
    )
    paths.append(p)

    p = OUT / "overseas_invoice" / "cursor_sample.pdf"
    make_pdf(
        p,
        "Cursor Pro Subscription",
        [
            "Invoice #in_test_001",
            "Date: 2026-05-01",
            "Amount: USD 20.00",
            "Period: 2026-05-01 to 2026-05-31",
            "From: billing@cursor.com",
            "To: customer@example.com",
            "Subscription: Cursor Pro (monthly)",
        ],
    )
    paths.append(p)

    p = OUT / "receipt" / "synthetic_receipt.pdf"
    make_pdf(
        p,
        "Receipt",
        [
            "Receipt for cash payment",
            "Date: 2026-05-01",
            "Amount: 150.00 CNY",
            "No tax id, no formal invoice number",
            "Paid in cash at the register",
        ],
    )
    paths.append(p)

    p = OUT / "proforma" / "synthetic_proforma.pdf"
    make_pdf(
        p,
        "PROFORMA INVOICE",
        [
            "NOT A TAX INVOICE",
            "Quote #2026-Q-001",
            "Estimated total: EUR 1200.00",
            "Issued before payment for budget approval",
            "Valid for 30 days from issue date",
        ],
    )
    paths.append(p)

    p = OUT / "other" / "synthetic_other.pdf"
    make_pdf(
        p,
        "Membership Fee",
        [
            "Annual community membership",
            "Date: 2026-05-01",
            "Amount: USD 50.00",
            "From: members@example-org.com",
            "To: member@example.com",
        ],
    )
    paths.append(p)

    return paths


if __name__ == "__main__":
    paths = generate_all()
    for p in paths:
        print(f"wrote {p}")
