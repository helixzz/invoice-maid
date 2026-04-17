from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class InvoiceExtract(BaseModel):
    """LLM-extracted invoice fields — used with instructor for structured output."""

    buyer: str = Field(description="购买方名称")
    seller: str = Field(description="销售方名称")
    invoice_no: str = Field(description="发票号码")
    invoice_date: date = Field(description="开票日期, YYYY-MM-DD")
    amount: Decimal = Field(gt=0, description="价税合计金额，纯数字")
    item_summary: str = Field(description="商品/服务一句话中文概括")
    invoice_type: str = Field(description="发票类型，如增值税电子普通发票、数电专票")
    confidence: float = Field(ge=0, le=1, default=0.9)


class EmailClassification(BaseModel):
    """LLM classification result."""

    is_invoice_related: bool = Field(description="邮件是否与发票相关")
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(description="判断依据，一句话")


class InvoiceResponse(BaseModel):
    """API response for a single invoice."""

    id: int
    invoice_no: str
    buyer: str
    seller: str
    amount: float
    invoice_date: str
    invoice_type: str
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
