# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnnecessaryCast=false
from __future__ import annotations

import base64
import io
import importlib
import logging
import re
import zipfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast

logger = logging.getLogger(__name__)

OFD_MAX_UNCOMPRESSED_BYTES: int = 100 * 1024 * 1024


@dataclass
class ParsedInvoice:
    """Result of parsing an invoice file."""

    invoice_no: str | None = None
    buyer: str | None = None
    seller: str | None = None
    amount: Decimal | None = None
    invoice_date: date | None = None
    invoice_type: str | None = None
    item_summary: str | None = None
    raw_text: str = ""
    source_format: Literal["pdf", "xml", "ofd"] = "pdf"
    extraction_method: Literal["qr", "xml_xpath", "ofd_struct", "llm", "regex"] = "llm"
    confidence: float = 0.0
    is_vat_document: bool = False


PATTERNS = {
    "invoice_code": re.compile(r"发票代码[：:\s]*(\d{10,12})"),
    "invoice_no": re.compile(r"发票号码[：:\s]*(\d{8,20})"),
    "invoice_date": re.compile(r"开票日期[：:\s]*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"),
    "buyer_name": re.compile(r"购\s*买\s*方.*?名\s*称[：:\s]*(.+?)(?:\n|纳税|统一)"),
    "seller_name": re.compile(r"销\s*售\s*方.*?名\s*称[：:\s]*(.+?)(?:\n|纳税|统一)"),
    "total_amount": re.compile(r"价税合计[（(]大写[）)].*?[¥￥]\s*([\d,]+\.\d{2})"),
    "total_amount_alt": re.compile(r"合\s*计.*?[¥￥]\s*([\d,]+\.\d{2})"),
}

_CID_RE = re.compile(r'\(cid:\d+\)')

_QR_VALID_TYPE_CODES: frozenset[str] = frozenset({"01", "03", "04", "08", "10", "11", "14", "15"})

_VAT_TYPE_MARKERS = [
    "增值税专用发票", "增值税普通发票", "电子发票（增值税专用发票）", "电子发票（普通发票）",
    "增值税电子专用发票", "增值税电子普通发票", "全面数字化的电子发票", "数电专票", "数电普票",
    "机动车销售统一发票",
]
_VAT_REQUIRED_MARKERS = ["价税合计", "税率", "税额", "发票号码"]
_NON_INVOICE_MARKERS = [
    "入住凭证", "住宿凭证", "结账单", "消费清单", "行程单", "出行记录", "用车凭证",
    "收款收据", "付款凭证",
]


def _has_cid_artifacts(text: str, threshold: int = 5) -> bool:
    return len(_CID_RE.findall(text)) > threshold


def detect_format(filename: str, content: bytes) -> Literal["pdf", "xml", "ofd"]:
    """Detect invoice format by filename extension and magic bytes."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext == "ofd" or content[:4] == b"PK\x03\x04":
        if ext == "ofd":
            return "ofd"

    if ext == "xml" or content.lstrip()[:5] == b"<?xml":
        return "xml"

    if ext == "pdf" or content[:4] == b"%PDF":
        return "pdf"

    if b"<?xml" in content[:100]:
        return "xml"

    return "pdf"


def _parse_date(year: str, month: str, day: str) -> date | None:
    try:
        return date(int(year), int(month), int(day))
    except (ValueError, TypeError):
        return None


def _parse_amount(amount_str: str) -> Decimal | None:
    try:
        cleaned = amount_str.replace(",", "").strip()
        return Decimal(cleaned)
    except (AttributeError, InvalidOperation, ValueError):
        return None


def _extract_text_from_pdf(content: bytes) -> str:
    """Extract text from PDF using pdfplumber."""
    try:
        pdfplumber = importlib.import_module("pdfplumber")

        with pdfplumber.open(io.BytesIO(content)) as pdf:
            texts: list[str] = []
            for page in pdf.pages[:5]:
                deduped = page.dedupe_chars(tolerance=1, extra_attrs=())
                text = deduped.extract_text(x_tolerance=2, y_tolerance=3)
                if text:
                    texts.append(text)
            return "\n".join(texts)
    except Exception as exc:
        logger.warning("pdfplumber extraction failed: %s", exc)
        return ""


def _extract_text_pymupdf(content: bytes) -> str:
    """Fallback text extraction via PyMuPDF."""
    try:
        fitz = importlib.import_module("fitz")

        doc = fitz.open(stream=content, filetype="pdf")
        try:
            texts = [page.get_text("text") for page in doc[:5]]
            return "\n".join(texts)
        finally:
            doc.close()
    except Exception as exc:
        logger.warning("PyMuPDF extraction failed: %s", exc)
        return ""


def _decode_qr_from_pdf(content: bytes) -> dict[str, str | None] | None:
    """Decode QR code from PDF page. Returns parsed fields or None."""
    try:
        fitz = importlib.import_module("fitz")
        image_module = importlib.import_module("PIL.Image")
        pyzbar_module = importlib.import_module("pyzbar.pyzbar")
        image_open = cast(Any, image_module.open)
        pyzbar_decode = cast(Any, pyzbar_module.decode)

        doc = fitz.open(stream=content, filetype="pdf")
        try:
            page = doc[0]
            mat = fitz.Matrix(3, 3)
            pix = page.get_pixmap(matrix=mat)
            img = image_open(io.BytesIO(pix.tobytes("png")))
            barcodes = pyzbar_decode(img)
        finally:
            doc.close()

        for barcode in barcodes:
            raw = barcode.data.decode("utf-8", errors="ignore").strip()
            if raw.startswith("http://") or raw.startswith("https://"):
                continue
            for sep in [",", "|"]:
                parts = [p.strip() for p in raw.split(sep)]
                if len(parts) >= 7 and parts[0] == "01" and parts[1] in _QR_VALID_TYPE_CODES:
                    return {
                        "invoice_code": parts[2] or None,
                        "invoice_no": parts[3] or None,
                        "amount_str": parts[4] or None,
                        "invoice_date_str": parts[5] or None,
                    }
                if len(parts) == 5 and re.match(r'^\d{10,12}$', parts[0]):
                    return {
                        "invoice_code": parts[0] or None,
                        "invoice_no": parts[1] or None,
                        "amount_str": parts[2] or None,
                        "invoice_date_str": parts[3] or None,
                    }
        return None
    except Exception as exc:
        logger.warning("QR code extraction failed: %s", exc)
        return None


def _parse_qr_date(date_str: str) -> date | None:
    """Parse date from QR code (YYYYMMDD or YYYY-MM-DD or YYYY年MM月DD日)."""
    date_str = date_str.strip()

    if re.match(r"^\d{8}$", date_str):
        return _parse_date(date_str[:4], date_str[4:6], date_str[6:8])

    match = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", date_str)
    if match:
        return _parse_date(match.group(1), match.group(2), match.group(3))

    match = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", date_str)
    if match:
        return _parse_date(match.group(1), match.group(2), match.group(3))

    return None


def _extract_from_regex(text: str) -> ParsedInvoice:
    """Extract invoice fields from text using regex patterns."""
    result = ParsedInvoice(raw_text=text, extraction_method="regex")

    match = PATTERNS["invoice_no"].search(text)
    if match:
        result.invoice_no = match.group(1)

    match = PATTERNS["invoice_date"].search(text)
    if match:
        result.invoice_date = _parse_date(match.group(1), match.group(2), match.group(3))

    match = PATTERNS["buyer_name"].search(text)
    if match:
        result.buyer = match.group(1).strip()

    match = PATTERNS["seller_name"].search(text)
    if match:
        result.seller = match.group(1).strip()

    match = PATTERNS["total_amount"].search(text)
    if not match:
        match = PATTERNS["total_amount_alt"].search(text)
    if match:
        result.amount = _parse_amount(match.group(1))

    result.confidence = _vat_confidence(
        result.invoice_no,
        result.buyer,
        result.seller,
        result.amount,
        result.invoice_date,
        result.invoice_type,
    )
    return result


def _vat_confidence(
    invoice_no: str | None,
    buyer: str | None,
    seller: str | None,
    amount: Decimal | None,
    invoice_date: date | None,
    invoice_type: str | None,
) -> float:
    from app.schemas.invoice import VALID_INVOICE_TYPES

    score = 0.0
    if invoice_no:
        score += 0.30
    if amount and amount > 0:
        score += 0.25
    if invoice_date:
        score += 0.15
    if buyer:
        score += 0.10
    if seller:
        score += 0.10
    if invoice_type and (
        invoice_type in VALID_INVOICE_TYPES
        or any(vt in invoice_type for vt in VALID_INVOICE_TYPES if len(vt) > 3)
    ):
        score += 0.10
    return round(min(score, 1.0), 2)


def _is_vat_document(text: str) -> bool:
    if not text:
        return False
    if any(m in text for m in _NON_INVOICE_MARKERS):
        return False
    if not any(m in text for m in _VAT_TYPE_MARKERS):
        return False
    return sum(1 for m in _VAT_REQUIRED_MARKERS if m in text) >= 2


def parse_pdf(content: bytes) -> ParsedInvoice:
    """Parse a PDF invoice. Strategy: QR code → regex → raw text for LLM fallback."""
    result = ParsedInvoice(source_format="pdf")

    qr_data = _decode_qr_from_pdf(content)
    if qr_data:
        result.extraction_method = "qr"
        result.invoice_no = qr_data.get("invoice_no")
        result.amount = _parse_amount(qr_data.get("amount_str", "") or "")
        result.invoice_date = _parse_qr_date(qr_data.get("invoice_date_str", "") or "")
        result.confidence = 0.95

    text = _extract_text_from_pdf(content)
    if not text or _has_cid_artifacts(text):
        logger.info("PDF parser falling back to PyMuPDF text extraction")
        text = _extract_text_pymupdf(content)
    result.raw_text = text

    if qr_data:
        regex_result = _extract_from_regex(text)
        if not result.buyer:
            result.buyer = regex_result.buyer
        if not result.seller:
            result.seller = regex_result.seller
        if not result.invoice_date:
            result.invoice_date = regex_result.invoice_date
        if not result.amount:
            result.amount = regex_result.amount
        if not result.invoice_type:
            result.invoice_type = regex_result.invoice_type
        if not result.item_summary:
            result.item_summary = regex_result.item_summary
        result.is_vat_document = _is_vat_document(result.raw_text)
        logger.debug("Parsed PDF invoice with method qr")
        return result

    regex_result = _extract_from_regex(text)
    regex_result.is_vat_document = _is_vat_document(regex_result.raw_text)
    if regex_result.confidence >= 0.6:
        regex_result.source_format = "pdf"
        logger.debug("Parsed PDF invoice with method regex")
        return regex_result

    result.extraction_method = "llm"
    result.confidence = 0.0
    result.is_vat_document = _is_vat_document(result.raw_text)
    logger.error("PDF parsing failed for file: all extraction strategies exhausted")
    return result


def parse_xml(content: bytes) -> ParsedInvoice:
    """Parse a Chinese VAT invoice XML file.

    Hardened against XXE / billion-laughs / external-DTD attacks by
    constructing an ``lxml.etree.XMLParser`` with ``resolve_entities=False``,
    ``no_network=True`` and ``huge_tree=False``. Stock
    ``lxml.etree.fromstring`` has safer-than-stdlib defaults but still
    resolves internal entity declarations, which is the exploit vector we
    want to close. We avoid ``defusedxml.lxml`` because upstream has
    deprecated the lxml backend (Python 3.13+ will drop it)."""
    result = ParsedInvoice(source_format="xml", extraction_method="xml_xpath")

    try:
        etree = importlib.import_module("lxml.etree")
        safe_parser = etree.XMLParser(
            resolve_entities=False,
            no_network=True,
            huge_tree=False,
            load_dtd=False,
        )

        try:
            root = etree.fromstring(content, parser=safe_parser)
        except etree.XMLSyntaxError:
            try:
                root = etree.fromstring(
                    content.decode("gbk", errors="replace").encode("utf-8"),
                    parser=safe_parser,
                )
            except Exception:
                result.raw_text = content.decode("utf-8", errors="ignore")
                result.confidence = 0.0
                return result
        result.raw_text = etree.tostring(root, encoding="unicode", pretty_print=True)

        def find_text(tag_names: list[str]) -> str | None:
            for tag in tag_names:
                element = root.find(f".//{tag}")
                if element is not None and element.text:
                    return element.text.strip()
                for elem in root.iter():
                    local_name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                    if local_name == tag and elem.text:
                        return elem.text.strip()
            return None

        result.invoice_no = find_text(["InvoiceNo", "fpHm", "invoiceNumber", "InvoiceNumber", "FPHM", "EInvoiceNumber"])
        result.buyer = find_text(["BuyerName", "gmfMc", "buyerName", "GMF_MC", "Gfmc", "GMFMC", "PayingPartyName"])
        result.seller = find_text(["SellerName", "xsfMc", "sellerName", "XSF_MC", "XSFMC", "InvoicingPartyName"])
        result.invoice_type = find_text(["InvoiceType", "fplx", "invoiceType", "FPLX", "KPLX", "EInvoiceName"])

        amount_str = find_text(["TaxInclusiveTotalAmount", "jshj", "totalAmount", "TotalAmount", "JSHJ", "TotalIncludingTax", "PriceTaxTotal", "价税合计"])
        if amount_str:
            result.amount = _parse_amount(amount_str)

        date_str = find_text(["InvoiceDate", "kprq", "invoiceDate", "IssueDate", "KPRQ", "IssueDateTime", "开票日期"])
        if date_str:
            result.invoice_date = _parse_qr_date(date_str)

        items: list[str] = []
        for item_tag in ["GoodsName", "xmmc", "goodsName", "Spmc", "XMMC", "ItemName"]:
            for elem in root.iter():
                local_name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                if local_name == item_tag and elem.text:
                    items.append(elem.text.strip())
        if items:
            result.item_summary = "; ".join(items[:5])

        result.confidence = _vat_confidence(
            result.invoice_no,
            result.buyer,
            result.seller,
            result.amount,
            result.invoice_date,
            result.invoice_type,
        )
        result.is_vat_document = _is_vat_document(result.raw_text)
        logger.debug("Parsed XML invoice with method xml_xpath")
    except Exception as exc:
        logger.error("XML parsing failed: %s", exc)
        result.raw_text = content.decode("utf-8", errors="ignore")
        result.confidence = 0.0

    return result


def parse_ofd(content: bytes) -> ParsedInvoice:
    """Parse an OFD (Open Fixed-layout Document) invoice.

    OFD files are ZIP containers, which makes them a zip-bomb vector if
    arbitrary uploads are accepted. Before handing the bytes to easyofd
    we enumerate ZIP central-directory entries and reject any file whose
    sum-of-uncompressed-sizes exceeds OFD_MAX_UNCOMPRESSED_BYTES (100 MB).
    This catches `42.zip`-style bombs without actually extracting them."""
    result = ParsedInvoice(source_format="ofd", extraction_method="ofd_struct")

    try:
        if content[:4] == b"PK\x03\x04":
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                total_uncompressed = sum(
                    max(info.file_size, 0) for info in zf.infolist()
                )
            if total_uncompressed > OFD_MAX_UNCOMPRESSED_BYTES:
                logger.warning(
                    "OFD rejected: uncompressed size %d > %d limit",
                    total_uncompressed,
                    OFD_MAX_UNCOMPRESSED_BYTES,
                )
                result.confidence = 0.0
                return result
    except zipfile.BadZipFile as exc:
        logger.warning("OFD rejected: invalid ZIP container (%s)", exc)
        result.confidence = 0.0
        return result

    try:
        ofd_module = importlib.import_module("easyofd.ofd")
        ofd_cls = cast(Any, ofd_module.OFD)

        ofd_b64 = base64.b64encode(content).decode("utf-8")
        ofd = ofd_cls()
        try:
            ofd.read(ofd_b64, save_xml=False)
            data = ofd.data

            if data and isinstance(data, list) and len(data) > 0:
                invoice_data = data[0] if isinstance(data[0], dict) else {}
                result.invoice_no = invoice_data.get("InvoiceNo") or invoice_data.get("发票号码")
                result.buyer = invoice_data.get("BuyerName") or invoice_data.get("购买方名称")
                result.seller = invoice_data.get("SellerName") or invoice_data.get("销售方名称")
                result.invoice_type = invoice_data.get("InvoiceType") or invoice_data.get("发票类型")

                amount_str = (
                    invoice_data.get("TaxInclusiveTotalAmount")
                    or invoice_data.get("价税合计")
                    or invoice_data.get("合计金额")
                )
                if amount_str:
                    result.amount = _parse_amount(str(amount_str))

                date_str = invoice_data.get("InvoiceDate") or invoice_data.get("开票日期")
                if date_str:
                    result.invoice_date = _parse_qr_date(str(date_str))

                result.raw_text = str(data)
                result.confidence = _vat_confidence(
                    result.invoice_no,
                    result.buyer,
                    result.seller,
                    result.amount,
                    result.invoice_date,
                    result.invoice_type,
                )
            else:
                result.confidence = 0.0
        finally:
            ofd.del_data()
        result.is_vat_document = _is_vat_document(result.raw_text)
        logger.debug("Parsed OFD invoice with method ofd_struct")
    except ImportError:
        logger.warning("easyofd not installed — OFD parsing unavailable")
        result.confidence = 0.0
    except Exception as exc:
        logger.error("OFD parsing failed: %s", exc)
        result.confidence = 0.0

    return result


def parse(filename: str, content: bytes) -> ParsedInvoice:
    """Main entry point: detect format and parse invoice."""
    fmt = detect_format(filename, content)

    if fmt == "pdf":
        return parse_pdf(content)
    if fmt == "xml":
        return parse_xml(content)
    if fmt == "ofd":
        return parse_ofd(content)
    raise ValueError(f"Unsupported invoice format: {fmt}")


class InvoiceParser:
    """Convenience parser service wrapper."""

    def parse(self, filename: str, content: bytes) -> ParsedInvoice:
        return parse(filename, content)

    def parse_pdf(self, content: bytes) -> ParsedInvoice:
        return parse_pdf(content)

    def parse_xml(self, content: bytes) -> ParsedInvoice:
        return parse_xml(content)

    def parse_ofd(self, content: bytes) -> ParsedInvoice:
        return parse_ofd(content)
