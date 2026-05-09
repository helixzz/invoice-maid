from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field, model_validator


VALID_INVOICE_TYPES: frozenset[str] = frozenset({
    "增值税专用发票", "增值税普通发票", "增值税电子专用发票", "增值税电子普通发票",
    "增值税普通发票（卷票）", "通行费发票",
    "电子发票（增值税专用发票）", "电子发票（普通发票）", "全面数字化的电子发票",
    "电子发票（铁路电子客票）", "电子发票（航空运输电子客票行程单）",
    "数电专票", "数电普票", "电子专票", "电子普票",
    "机动车销售统一发票", "二手车销售统一发票",
    "01", "03", "04", "08", "10", "11", "14", "15", "0910", "0920",
})


TRANSPORT_E_TICKET_TYPES: frozenset[str] = frozenset({
    "电子发票（铁路电子客票）",
    "电子发票（航空运输电子客票行程单）",
})


class InvoiceCategory(str, Enum):
    """v1.2.0 Track A — structured document-kind taxonomy.

    Orthogonal to both ``invoice_type`` (freeform display label) and
    ``is_valid_tax_invoice`` (VAT-validity boolean). The enum is the
    authoritative contract — SQLite enforces via Pydantic at the API
    boundary and the LLM prompt's STEP 0 classifier, not via DB CHECK
    (see migration 0015 rationale)."""

    VAT_INVOICE = "vat_invoice"
    RECEIPT = "receipt"
    PROFORMA = "proforma"
    SAAS_INVOICE = "saas_invoice"
    OTHER = "other"


class InvoiceExtract(BaseModel):
    """LLM-extracted invoice fields — used with instructor for structured output."""

    buyer: str = Field(description="购买方名称")
    seller: str = Field(description="销售方名称")
    invoice_no: str = Field(description="发票号码")
    invoice_date: date = Field(description="开票日期, YYYY-MM-DD")
    amount: Decimal = Field(ge=0, description="价税合计金额，纯数字。对于图片型铁路/航空电子客票若无法读取可填 0.01 标记占位")
    item_summary: str = Field(description="商品/服务一句话中文概括")
    invoice_type: str = Field(description="发票类型，如增值税电子普通发票、数电专票、电子发票（铁路电子客票）")
    invoice_category: InvoiceCategory = Field(
        default=InvoiceCategory.VAT_INVOICE,
        description=(
            "Document category taxonomy. Determined by STEP 0 of the "
            "extract prompt. Legacy cache entries without this field "
            "default to vat_invoice (safe for the 250 pre-v1.2.0 "
            "production invoices, all Chinese VAT)."
        ),
    )
    confidence: float = Field(ge=0, le=1, default=0.9)
    is_valid_tax_invoice: bool = Field(
        default=False,
        description=(
            "True when the document is any legally-recognised Chinese VAT tax "
            "invoice, INCLUDING:\n"
            "• Traditional 增值税发票 (专票/普票/电子发票)\n"
            "• 数电票 / 全面数字化的电子发票\n"
            "• 电子发票（铁路电子客票）— per 2024年第8号公告 (2024-11-01起)\n"
            "• 电子发票（航空运输电子客票行程单）— per 2024年第9号公告 (2024-12-01起)\n"
            "False for hotel receipts, ride itineraries (滴滴/曹操出行), payment "
            "receipts, foreign-currency receipts, or any non-invoice document."
        ),
    )


class InvoicePlatform(str, Enum):
    NUONUO = "nuonuo"
    BAIWANG = "baiwang"
    AISINO = "aisino"
    CHINATAX = "chinatax"
    JD = "jd"
    ALIPAY = "alipay"
    OTHER = "other"
    UNKNOWN = "unknown"


class InvoiceFormat(str, Enum):
    PDF = "pdf"
    XML = "xml"
    OFD = "ofd"


class UrlKind(str, Enum):
    DIRECT_FILE = "direct_file"
    PLATFORM_DOWNLOAD = "platform_download"
    SAFELINK_WRAPPED = "safelink_wrapped"
    NONE = "none"


class ExtractionHints(BaseModel):
    platform: InvoicePlatform = InvoicePlatform.UNKNOWN
    likely_formats: list[InvoiceFormat] = Field(default_factory=list)
    invoice_type_hint: str | None = None
    visible_invoice_no: str | None = None
    visible_invoice_date: str | None = None
    visible_amount: str | None = None
    parser_notes: str | None = Field(default=None, description="≤80字事实性提示")


class EmailAnalysis(BaseModel):
    is_invoice_related: bool
    invoice_confidence: float = Field(ge=0.0, le=1.0)
    best_download_url: str | None = Field(default=None)
    url_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    url_is_safelink: bool = Field(default=False)
    url_kind: UrlKind = Field(default=UrlKind.NONE)
    extraction_hints: ExtractionHints = Field(default_factory=ExtractionHints)
    skip_reason: str | None = Field(default=None)

    @model_validator(mode="after")
    def validate_consistency(self) -> "EmailAnalysis":
        if self.is_invoice_related:
            if self.skip_reason is not None:
                raise ValueError("skip_reason must be null when is_invoice_related=true")
        else:
            if not self.skip_reason:
                raise ValueError("skip_reason required when is_invoice_related=false")
            if self.best_download_url is not None:
                raise ValueError("best_download_url must be null when not invoice")
        if self.best_download_url is None:
            if self.url_confidence != 0.0:
                raise ValueError("url_confidence must be 0 when no url")
            if self.url_is_safelink:
                raise ValueError("url_is_safelink must be false when no url")
            if self.url_kind != UrlKind.NONE:
                raise ValueError("url_kind must be none when no url")
        return self

    @property
    def should_download(self) -> bool:
        return self.best_download_url is not None and self.url_confidence >= 0.6


EmailClassification = EmailAnalysis


class InvoiceResponse(BaseModel):
    """API response for a single invoice."""

    id: int
    invoice_no: str
    buyer: str
    seller: str
    amount: float
    invoice_date: str
    invoice_type: str
    invoice_category: str
    item_summary: str | None
    source_format: str
    extraction_method: str
    confidence: float
    is_manually_corrected: bool
    created_at: str


class InvoiceListResponse(BaseModel):
    items: list[InvoiceResponse]
    total: int
    page: int
    size: int
