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
    class DedupedPage:
        def extract_text(self, **_kwargs):
            return "page1"

    class Page:
        def dedupe_chars(self, tolerance, extra_attrs):
            assert tolerance == 1
            assert extra_attrs == ()
            return DedupedPage()

    class PdfCtx:
        def __enter__(self):
            return SimpleNamespace(pages=[Page()])

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(parser.importlib, "import_module", lambda name: SimpleNamespace(open=lambda stream: PdfCtx()) if name == "pdfplumber" else SimpleNamespace())
    assert parser._extract_text_from_pdf(b"pdf") == "page1"

    class MultiDedupedPage:
        def __init__(self, value):
            self.value = value

        def extract_text(self, **_kwargs):
            return self.value

    class MultiPage:
        def __init__(self, value):
            self.value = value

        def dedupe_chars(self, tolerance, extra_attrs):
            assert tolerance == 1
            assert extra_attrs == ()
            return MultiDedupedPage(self.value)

    class MultiPageCtx:
        def __enter__(self):
            return SimpleNamespace(
                pages=[
                    MultiPage("page1"),
                    MultiPage(None),
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
            return SimpleNamespace(decode=lambda img: [Barcode("https://skip.me"), Barcode("01,03,code,no,88.88,20240102,extra")])
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
        data = b"123456789012,no,88.88,20240102,x"

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
    assert result == {"invoice_code": "123456789012", "invoice_no": "no", "amount_str": "88.88", "invoice_date_str": "20240102"}


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
        "增值税电子普通发票\n"
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
    assert result.confidence == 0.9


def test_has_cid_artifacts_vat_confidence_and_vat_document() -> None:
    assert parser._has_cid_artifacts("(cid:1)" * 6) is True
    assert parser._has_cid_artifacts("(cid:1)" * 5) is False
    assert parser._vat_confidence("12345678", "甲方", "乙方", Decimal("1.00"), date(2024, 1, 2), "增值税电子普通发票") == 1.0
    assert parser._vat_confidence(None, None, None, None, None, None) == 0.0
    assert parser._is_vat_document("增值税普通发票 价税合计 税额 发票号码") is True
    assert parser._is_vat_document("入住凭证 增值税普通发票 价税合计 税额 发票号码") is False
    assert parser._is_vat_document("普通单据 价税合计 税额 发票号码") is False


def test_parse_pdf_qr_regex_and_llm_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    debug_logs: list[str] = []
    info_logs: list[str] = []
    error_logs: list[str] = []
    monkeypatch.setattr(parser.logger, "debug", lambda message, *args: debug_logs.append(message % args))
    monkeypatch.setattr(parser.logger, "info", lambda message, *args: info_logs.append(message % args))
    monkeypatch.setattr(parser.logger, "error", lambda message, *args: error_logs.append(message % args))

    monkeypatch.setattr(parser, "_decode_qr_from_pdf", lambda content: {"invoice_no": "001", "amount_str": "88.00", "invoice_date_str": "20240102"})
    monkeypatch.setattr(parser, "_extract_text_from_pdf", lambda content: "增值税普通发票\n价税合计\n税额\n发票号码\n购买方名称：甲方\n纳税人识别号\n销售方名称：乙方\n纳税人识别号")
    monkeypatch.setattr(parser, "_extract_text_pymupdf", lambda content: "")
    result = parser.parse_pdf(b"pdf")
    assert result.extraction_method == "qr"
    assert result.invoice_no == "001"
    assert result.buyer == "甲方"
    assert result.is_vat_document is True
    assert debug_logs[-1] == "Parsed PDF invoice with method qr"

    monkeypatch.setattr(parser, "_decode_qr_from_pdf", lambda content: None)
    monkeypatch.setattr(
        parser,
        "_extract_text_from_pdf",
        lambda content: "增值税电子普通发票\n发票号码：12345678\n开票日期：2024年1月2日\n购买方名称：甲方\n纳税人识别号\n销售方名称：乙方\n纳税人识别号\n价税合计（大写） ￥1.00",
    )
    regex_result = parser.parse_pdf(b"pdf")
    assert regex_result.extraction_method == "regex"
    assert regex_result.source_format == "pdf"
    assert debug_logs[-1] == "Parsed PDF invoice with method regex"

    monkeypatch.setattr(parser, "_extract_text_from_pdf", lambda content: "")
    monkeypatch.setattr(parser, "_extract_text_pymupdf", lambda content: "")
    llm_result = parser.parse_pdf(b"pdf")
    assert llm_result.extraction_method == "llm"
    assert llm_result.confidence == 0.0
    assert info_logs[-1] == "PDF parser falling back to PyMuPDF text extraction"
    assert error_logs[-1] == "PDF parsing failed for file: all extraction strategies exhausted"


def test_parse_pdf_falls_back_on_cid_artifacts(monkeypatch: pytest.MonkeyPatch) -> None:
    info_logs: list[str] = []
    monkeypatch.setattr(parser.logger, "info", lambda message, *args: info_logs.append(message % args))
    monkeypatch.setattr(parser, "_decode_qr_from_pdf", lambda content: None)
    monkeypatch.setattr(parser, "_extract_text_from_pdf", lambda content: "(cid:1)" * 6)
    monkeypatch.setattr(parser, "_extract_text_pymupdf", lambda content: "增值税普通发票 发票号码：12345678 价税合计（大写） ￥1.00")
    result = parser.parse_pdf(b"pdf")
    assert result.raw_text == "增值税普通发票 发票号码：12345678 价税合计（大写） ￥1.00"
    assert info_logs[-1] == "PDF parser falling back to PyMuPDF text extraction"


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
    debug_logs: list[str] = []
    monkeypatch.setattr(parser.logger, "debug", lambda message, *args: debug_logs.append(message % args))

    class Root:
        def find(self, path):
            mapping = {
                ".//InvoiceNo": SimpleNamespace(text="001"),
                ".//BuyerName": None,
                ".//SellerName": None,
                ".//InvoiceType": SimpleNamespace(text="增值税电子普通发票"),
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

    etree = SimpleNamespace(
        fromstring=lambda content, parser=None: Root(),
        tostring=lambda root, encoding, pretty_print: "增值税电子普通发票 发票号码 价税合计 税额",
        XMLParser=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(parser.importlib, "import_module", lambda name: etree)
    result = parser.parse_xml(b"<xml />")
    assert result.invoice_no == "001"
    assert result.buyer == "甲方"
    assert result.seller == "乙方"
    assert result.item_summary == "办公用品"
    assert result.is_vat_document is True
    assert debug_logs[-1] == "Parsed XML invoice with method xml_xpath"

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

    etree = SimpleNamespace(
        fromstring=lambda content, parser=None: Root(),
        tostring=lambda root, encoding, pretty_print: "<xml />",
        XMLParser=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(parser.importlib, "import_module", lambda name: etree)
    result = parser.parse_xml(b"<xml />")
    assert result.invoice_no is None
    assert result.item_summary is None


def test_parse_xml_gbk_fallback_and_xmlsyntaxerror(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeXMLSyntaxError(Exception):
        pass

    calls: list[bytes] = []

    class Root:
        def find(self, path):
            mapping = {
                ".//EInvoiceNumber": SimpleNamespace(text="12345678"),
                ".//PayingPartyName": SimpleNamespace(text="甲方"),
                ".//InvoicingPartyName": SimpleNamespace(text="乙方"),
                ".//EInvoiceName": SimpleNamespace(text="增值税电子普通发票"),
                ".//TotalIncludingTax": SimpleNamespace(text="88.00"),
                ".//IssueDateTime": SimpleNamespace(text="2024-01-02"),
            }
            return mapping.get(path)

        def iter(self):
            return iter([SimpleNamespace(tag="ItemName", text="服务费")])

    def fromstring(content, parser=None):
        calls.append(content)
        if len(calls) == 1:
            raise FakeXMLSyntaxError("bad encoding")
        return Root()

    etree = SimpleNamespace(
        XMLSyntaxError=FakeXMLSyntaxError,
        fromstring=fromstring,
        tostring=lambda root, encoding, pretty_print: "增值税电子普通发票 发票号码 价税合计 税额",
        XMLParser=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(parser.importlib, "import_module", lambda name: etree)
    result = parser.parse_xml("中文".encode("gbk"))
    assert len(calls) == 2
    assert result.invoice_no == "12345678"
    assert result.item_summary == "服务费"
    assert result.is_vat_document is True


def test_parse_xml_returns_raw_text_when_gbk_fallback_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeXMLSyntaxError(Exception):
        pass

    def fromstring(_content, parser=None):
        raise FakeXMLSyntaxError("bad")

    etree = SimpleNamespace(
        XMLSyntaxError=FakeXMLSyntaxError,
        fromstring=fromstring,
        XMLParser=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(parser.importlib, "import_module", lambda name: etree)
    result = parser.parse_xml(b"<bad />")
    assert result.raw_text == "<bad />"
    assert result.confidence == 0.0


def test_parse_ofd_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    debug_logs: list[str] = []
    monkeypatch.setattr(parser.logger, "debug", lambda message, *args: debug_logs.append(message % args))

    class FakeOFD:
        def __init__(self):
            self.data = [{"InvoiceNo": "001", "BuyerName": "甲方", "SellerName": "乙方", "InvoiceType": "增值税电子普通发票", "TaxInclusiveTotalAmount": "99.00", "InvoiceDate": "20240102"}]

        def read(self, ofd_b64, save_xml=False):
            del ofd_b64, save_xml
            return None

        def del_data(self):
            return None

    monkeypatch.setattr(parser.importlib, "import_module", lambda name: SimpleNamespace(OFD=FakeOFD))
    result = parser.parse_ofd(b"ofd")
    assert result.invoice_no == "001"
    assert result.confidence == 1.0
    assert result.is_vat_document is False
    assert debug_logs[-1] == "Parsed OFD invoice with method ofd_struct"

    class EmptyOFD(FakeOFD):
        def __init__(self):
            self.data = []

    monkeypatch.setattr(parser.importlib, "import_module", lambda name: SimpleNamespace(OFD=EmptyOFD))
    assert parser.parse_ofd(b"ofd").confidence == 0.0
    assert debug_logs[-1] == "Parsed OFD invoice with method ofd_struct"

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
