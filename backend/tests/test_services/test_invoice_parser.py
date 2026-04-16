from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.services.invoice_parser as parser
from app.services.invoice_parser import InvoiceParser, ParsedInvoice


def test_detect_format_and_basic_parsers() -> None:
    assert parser.detect_format("a.ofd", b"PK\x03\x04rest") == "ofd"
    assert parser.detect_format("a.bin", b"PK\x03\x04rest") == "pdf"
    assert parser.detect_format("a.xml", b"<?xml version='1.0'?>") == "xml"
    assert parser.detect_format("a.pdf", b"%PDF-1.7") == "pdf"
    assert parser.detect_format("a.bin", b"xxx<?xml rest") == "xml"
    assert parser.detect_format("a.bin", b"other") == "pdf"
    assert parser._parse_date("2024", "1", "2") == date(2024, 1, 2)
    assert parser._parse_date("2024", "13", "2") is None
    assert parser._parse_amount("1,234.50") == Decimal("1234.50")
    assert parser._parse_amount(None) is None
    assert parser._parse_qr_date("20240102") == date(2024, 1, 2)
    assert parser._parse_qr_date("2024-01-02") == date(2024, 1, 2)
    assert parser._parse_qr_date("2024年1月2日") == date(2024, 1, 2)
    assert parser._parse_qr_date("bad") is None


def test_extract_text_from_pdf_and_pymupdf(monkeypatch: pytest.MonkeyPatch) -> None:
    class PdfCtx:
        def __enter__(self):
            return SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda **kwargs: "page1")])

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(parser.importlib, "import_module", lambda name: SimpleNamespace(open=lambda stream: PdfCtx()) if name == "pdfplumber" else SimpleNamespace())
    assert parser._extract_text_from_pdf(b"pdf") == "page1"

    class MultiPageCtx:
        def __enter__(self):
            return SimpleNamespace(
                pages=[
                    SimpleNamespace(extract_text=lambda **kwargs: "page1"),
                    SimpleNamespace(extract_text=lambda **kwargs: None),
                ]
            )

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(parser.importlib, "import_module", lambda name: SimpleNamespace(open=lambda stream: MultiPageCtx()) if name == "pdfplumber" else SimpleNamespace())
    assert parser._extract_text_from_pdf(b"pdf") == "page1"

    def broken_import(name):
        del name
        raise RuntimeError("fail")

    monkeypatch.setattr(parser.importlib, "import_module", broken_import)
    assert parser._extract_text_from_pdf(b"pdf") == ""

    class Doc(list):
        def close(self):
            self.closed = True

    monkeypatch.setattr(
        parser.importlib,
        "import_module",
        lambda name: SimpleNamespace(open=lambda **kwargs: Doc([SimpleNamespace(get_text=lambda mode: "page1")])) if name == "fitz" else SimpleNamespace(),
    )
    assert parser._extract_text_pymupdf(b"pdf") == "page1"
    monkeypatch.setattr(parser.importlib, "import_module", broken_import)
    assert parser._extract_text_pymupdf(b"pdf") == ""


def test_decode_qr_from_pdf_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    class Doc:
        def __getitem__(self, index):
            return SimpleNamespace(get_pixmap=lambda matrix: SimpleNamespace(tobytes=lambda fmt: b"png"))

        def close(self):
            return None

    class Barcode:
        def __init__(self, payload):
            self.data = payload.encode()

    def import_module(name):
        if name == "fitz":
            return SimpleNamespace(open=lambda **kwargs: Doc(), Matrix=lambda x, y: (x, y))
        if name == "PIL.Image":
            return SimpleNamespace(open=lambda stream: "image")
        if name == "pyzbar.pyzbar":
            return SimpleNamespace(decode=lambda img: [Barcode("x,code,no,20240102,88.88,extra"), Barcode("code|no|88.88|20240102|x")])
        raise AssertionError(name)

    monkeypatch.setattr(parser.importlib, "import_module", import_module)
    result = parser._decode_qr_from_pdf(b"pdf")
    assert result and result["invoice_no"] == "no"

    def broken(name):
        del name
        raise RuntimeError("bad")

    monkeypatch.setattr(parser.importlib, "import_module", broken)
    assert parser._decode_qr_from_pdf(b"pdf") is None


def test_decode_qr_from_pdf_five_part_path(monkeypatch: pytest.MonkeyPatch) -> None:
    class Doc:
        def __getitem__(self, index):
            return SimpleNamespace(get_pixmap=lambda matrix: SimpleNamespace(tobytes=lambda fmt: b"png"))

        def close(self):
            return None

    class Barcode:
        data = b"code,no,88.88,20240102,x"

    def import_module(name):
        if name == "fitz":
            return SimpleNamespace(open=lambda **kwargs: Doc(), Matrix=lambda x, y: (x, y))
        if name == "PIL.Image":
            return SimpleNamespace(open=lambda stream: "image")
        if name == "pyzbar.pyzbar":
            return SimpleNamespace(decode=lambda img: [Barcode()])
        raise AssertionError(name)

    monkeypatch.setattr(parser.importlib, "import_module", import_module)
    result = parser._decode_qr_from_pdf(b"pdf")
    assert result == {"invoice_code": "code", "invoice_no": "no", "amount_str": "88.88", "invoice_date_str": "20240102"}


def test_decode_qr_from_pdf_returns_none_for_invalid_barcodes(monkeypatch: pytest.MonkeyPatch) -> None:
    class Doc:
        def __getitem__(self, index):
            return SimpleNamespace(get_pixmap=lambda matrix: SimpleNamespace(tobytes=lambda fmt: b"png"))

        def close(self):
            return None

    class Barcode:
        data = b"invalid"

    def import_module(name):
        if name == "fitz":
            return SimpleNamespace(open=lambda **kwargs: Doc(), Matrix=lambda x, y: (x, y))
        if name == "PIL.Image":
            return SimpleNamespace(open=lambda stream: "image")
        if name == "pyzbar.pyzbar":
            return SimpleNamespace(decode=lambda img: [Barcode()])
        raise AssertionError(name)

    monkeypatch.setattr(parser.importlib, "import_module", import_module)
    assert parser._decode_qr_from_pdf(b"pdf") is None


def test_extract_from_regex() -> None:
    text = (
        "发票号码：12345678\n"
        "开票日期：2024年1月2日\n"
        "购买方名称：甲方\n纳税人识别号\n"
        "销售方名称：乙方\n纳税人识别号\n"
        "价税合计（大写） ￥1,234.50"
    )
    result = parser._extract_from_regex(text)
    assert result.invoice_no == "12345678"
    assert result.buyer == "甲方"
    assert result.seller == "乙方"
    assert result.amount == Decimal("1234.50")
    assert result.invoice_date == date(2024, 1, 2)
    assert result.confidence == 1.0


def test_parse_pdf_qr_regex_and_llm_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parser, "_decode_qr_from_pdf", lambda content: {"invoice_no": "001", "amount_str": "88.00", "invoice_date_str": "20240102"})
    monkeypatch.setattr(parser, "_extract_text_from_pdf", lambda content: "购买方名称：甲方\n纳税人识别号\n销售方名称：乙方\n纳税人识别号")
    monkeypatch.setattr(parser, "_extract_text_pymupdf", lambda content: "")
    result = parser.parse_pdf(b"pdf")
    assert result.extraction_method == "qr"
    assert result.invoice_no == "001"
    assert result.buyer == "甲方"

    monkeypatch.setattr(parser, "_decode_qr_from_pdf", lambda content: None)
    monkeypatch.setattr(
        parser,
        "_extract_text_from_pdf",
        lambda content: "发票号码：12345678\n购买方名称：甲方\n纳税人识别号\n销售方名称：乙方\n纳税人识别号\n价税合计（大写） ￥1.00",
    )
    regex_result = parser.parse_pdf(b"pdf")
    assert regex_result.extraction_method == "regex"
    assert regex_result.source_format == "pdf"

    monkeypatch.setattr(parser, "_extract_text_from_pdf", lambda content: "")
    monkeypatch.setattr(parser, "_extract_text_pymupdf", lambda content: "")
    llm_result = parser.parse_pdf(b"pdf")
    assert llm_result.extraction_method == "llm"
    assert llm_result.confidence == 0.0


def test_parse_pdf_qr_fills_remaining_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parser, "_decode_qr_from_pdf", lambda content: {"invoice_no": "001", "amount_str": "", "invoice_date_str": ""})
    monkeypatch.setattr(parser, "_extract_text_from_pdf", lambda content: "text")
    monkeypatch.setattr(
        parser,
        "_extract_from_regex",
        lambda text: ParsedInvoice(buyer="甲方", seller="乙方", amount=Decimal("20.00"), invoice_date=date(2024, 1, 2), invoice_type="类型", item_summary="摘要"),
    )
    result = parser.parse_pdf(b"pdf")
    assert result.invoice_date == date(2024, 1, 2)
    assert result.amount == Decimal("20.00")
    assert result.invoice_type == "类型"
    assert result.item_summary == "摘要"


def test_parse_pdf_qr_preserves_existing_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    original_parsed_invoice = parser.ParsedInvoice

    class PrefilledParsedInvoice(original_parsed_invoice):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.buyer = "已有购买方"
            self.seller = "已有销售方"
            self.invoice_type = "已有类型"
            self.item_summary = "已有摘要"

    monkeypatch.setattr(parser, "ParsedInvoice", PrefilledParsedInvoice)
    monkeypatch.setattr(parser, "_decode_qr_from_pdf", lambda content: {"invoice_no": "001", "amount_str": "20.00", "invoice_date_str": "20240102"})
    monkeypatch.setattr(parser, "_extract_text_from_pdf", lambda content: "text")
    monkeypatch.setattr(
        parser,
        "_extract_from_regex",
        lambda text: ParsedInvoice(buyer="甲方", seller="乙方", amount=Decimal("30.00"), invoice_date=date(2024, 1, 3), invoice_type="类型", item_summary="摘要"),
    )
    result = parser.parse_pdf(b"pdf")
    assert result.amount == Decimal("20.00")
    assert result.invoice_date == date(2024, 1, 2)
    assert result.buyer == "已有购买方"
    assert result.seller == "已有销售方"
    assert result.invoice_type == "已有类型"
    assert result.item_summary == "已有摘要"


def test_parse_xml_success_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class Root:
        def find(self, path):
            mapping = {
                ".//InvoiceNo": SimpleNamespace(text="001"),
                ".//BuyerName": None,
                ".//SellerName": None,
                ".//InvoiceType": SimpleNamespace(text="电子票"),
                ".//TaxInclusiveTotalAmount": SimpleNamespace(text="66.00"),
                ".//InvoiceDate": SimpleNamespace(text="20240102"),
            }
            return mapping.get(path)

        def iter(self):
            return iter(
                [
                    SimpleNamespace(tag="{x}BuyerName", text="甲方"),
                    SimpleNamespace(tag="SellerName", text="乙方"),
                    SimpleNamespace(tag="goodsName", text="办公用品"),
                ]
            )

    etree = SimpleNamespace(fromstring=lambda content: Root(), tostring=lambda root, encoding, pretty_print: "<xml />")
    monkeypatch.setattr(parser.importlib, "import_module", lambda name: etree)
    result = parser.parse_xml(b"<xml />")
    assert result.invoice_no == "001"
    assert result.buyer == "甲方"
    assert result.seller == "乙方"
    assert result.item_summary == "办公用品"

    monkeypatch.setattr(parser.importlib, "import_module", lambda name: (_ for _ in ()).throw(RuntimeError("bad")))
    failed = parser.parse_xml(b"<xml />")
    assert failed.raw_text == "<xml />"
    assert failed.confidence == 0.0


def test_parse_xml_missing_optional_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    class Root:
        def find(self, path):
            del path
            return None

        def iter(self):
            return iter([])

    etree = SimpleNamespace(fromstring=lambda content: Root(), tostring=lambda root, encoding, pretty_print: "<xml />")
    monkeypatch.setattr(parser.importlib, "import_module", lambda name: etree)
    result = parser.parse_xml(b"<xml />")
    assert result.invoice_no is None
    assert result.item_summary is None


def test_parse_ofd_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeOFD:
        def __init__(self):
            self.data = [{"InvoiceNo": "001", "BuyerName": "甲方", "SellerName": "乙方", "TaxInclusiveTotalAmount": "99.00", "InvoiceDate": "20240102"}]

        def read(self, ofd_b64, save_xml=False):
            del ofd_b64, save_xml
            return None

        def del_data(self):
            return None

    monkeypatch.setattr(parser.importlib, "import_module", lambda name: SimpleNamespace(OFD=FakeOFD))
    result = parser.parse_ofd(b"ofd")
    assert result.invoice_no == "001"
    assert result.confidence == 1.0

    class EmptyOFD(FakeOFD):
        def __init__(self):
            self.data = []

    monkeypatch.setattr(parser.importlib, "import_module", lambda name: SimpleNamespace(OFD=EmptyOFD))
    assert parser.parse_ofd(b"ofd").confidence == 0.0

    def raise_import_error(name):
        del name
        raise ImportError("missing")

    monkeypatch.setattr(parser.importlib, "import_module", raise_import_error)
    assert parser.parse_ofd(b"ofd").confidence == 0.0

    monkeypatch.setattr(parser.importlib, "import_module", lambda name: (_ for _ in ()).throw(RuntimeError("bad")))
    assert parser.parse_ofd(b"ofd").confidence == 0.0


def test_parse_ofd_without_amount_or_date(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeOFD:
        def __init__(self):
            self.data = [{"InvoiceNo": "001", "BuyerName": "甲方", "SellerName": "乙方"}]

        def read(self, ofd_b64, save_xml=False):
            del ofd_b64, save_xml

        def del_data(self):
            return None

    monkeypatch.setattr(parser.importlib, "import_module", lambda name: SimpleNamespace(OFD=FakeOFD))
    result = parser.parse_ofd(b"ofd")
    assert result.amount is None
    assert result.invoice_date is None


def test_parse_dispatch_and_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parser, "parse_pdf", lambda content: ParsedInvoice(invoice_no="pdf"))
    monkeypatch.setattr(parser, "parse_xml", lambda content: ParsedInvoice(invoice_no="xml"))
    monkeypatch.setattr(parser, "parse_ofd", lambda content: ParsedInvoice(invoice_no="ofd"))
    monkeypatch.setattr(parser, "detect_format", lambda filename, content: "pdf")
    assert parser.parse("a.pdf", b"x").invoice_no == "pdf"
    monkeypatch.setattr(parser, "detect_format", lambda filename, content: "xml")
    assert parser.parse("a.xml", b"x").invoice_no == "xml"
    monkeypatch.setattr(parser, "detect_format", lambda filename, content: "ofd")
    assert parser.parse("a.ofd", b"x").invoice_no == "ofd"
    monkeypatch.setattr(parser, "detect_format", lambda filename, content: "other")
    with pytest.raises(ValueError, match="Unsupported invoice format"):
        parser.parse("a.bin", b"x")

    wrapper = InvoiceParser()
    monkeypatch.setattr(parser, "parse", lambda filename, content: ParsedInvoice(invoice_no="wrapped"))
    monkeypatch.setattr(parser, "parse_pdf", lambda content: ParsedInvoice(invoice_no="pdf"))
    monkeypatch.setattr(parser, "parse_xml", lambda content: ParsedInvoice(invoice_no="xml"))
    monkeypatch.setattr(parser, "parse_ofd", lambda content: ParsedInvoice(invoice_no="ofd"))
    assert wrapper.parse("a.pdf", b"x").invoice_no == "wrapped"
    assert wrapper.parse_pdf(b"x").invoice_no == "pdf"
    assert wrapper.parse_xml(b"x").invoice_no == "xml"
    assert wrapper.parse_ofd(b"x").invoice_no == "ofd"
