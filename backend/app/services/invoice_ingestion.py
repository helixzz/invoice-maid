"""Shared invoice enrichment, VLM fallback, currency resolution, and validation pipeline.

Used by both ``app.tasks.scheduler`` (email / scraper ingestion) and
``app.services.manual_upload`` (web batch upload) so every channel enforces
identical extraction, VLM fallback, multi-currency, anti-scam, and VAT rules.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
import logging
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.schemas.invoice import InvoiceCategory, TRANSPORT_E_TICKET_TYPES, VALID_INVOICE_TYPES
from app.services.email_classifier import is_scam_text
from app.services.invoice_parser import (
    ParsedInvoice,
    detect_currency,
    normalize_currency,
    render_pdf_pages_to_base64_png,
    restore_legal_seller_name,
)

logger = logging.getLogger(__name__)


@dataclass
class IngestionVerdict:
    accepted: bool
    rejection_outcome: Literal["scam_detected", "not_vat_invoice", "low_confidence"] | None = None
    extraction_log_outcome: str | None = None
    reason: str = ""
    detail: str = ""
    final_category: str = "vat_invoice"
    final_currency: str = "CNY"


def is_transport_e_ticket(parsed: ParsedInvoice) -> bool:
    """True when ``parsed`` is a Chinese railway or airline e-ticket (2024年第8/9号公告)."""
    invoice_type = parsed.invoice_type or ""
    if invoice_type in TRANSPORT_E_TICKET_TYPES:
        return True
    item_summary = parsed.item_summary or ""
    raw_text = parsed.raw_text or ""
    transport_markers = ("铁路电子客票", "铁路客运", "航空运输电子客票行程单", "电子行程单")
    invoice_no = parsed.invoice_no or ""
    has_digital_invoice_no = len(invoice_no) == 20 and invoice_no.isdigit()
    if not has_digital_invoice_no:
        return False
    for marker in transport_markers:
        if marker in invoice_type or marker in item_summary or marker in raw_text:
            return True
    return False


def needs_llm_enrichment(parsed: ParsedInvoice) -> tuple[bool, bool]:
    """Return ``(should_enrich, amount_is_sentinel)``."""
    amount_is_sentinel = parsed.amount is not None and parsed.amount < Decimal("0.10")
    fields_missing = (
        not parsed.buyer
        or parsed.buyer == "未知"
        or not parsed.seller
        or parsed.seller == "未知"
        or not parsed.invoice_type
        or parsed.invoice_type == "未知"
        or not parsed.item_summary
    )
    should_enrich = (
        parsed.confidence < 0.6
        or not parsed.invoice_no
        or amount_is_sentinel
        or fields_missing
    )
    return should_enrich, amount_is_sentinel


def merge_llm_into_parsed(
    parsed: ParsedInvoice,
    extracted: Any,
    *,
    method_label: Literal["llm", "vlm"] = "llm",
) -> bool:
    """Selective merge of LLM/VLM fields into ``parsed``. Returns updated ``amount_is_sentinel``."""
    parser_invoice_no_looks_valid = bool(
        parsed.invoice_no
        and parsed.invoice_no.isdigit()
        and len(parsed.invoice_no) in (8, 20)
    )
    amount_is_sentinel = parsed.amount is not None and parsed.amount < Decimal("0.10")

    if extracted.buyer and extracted.buyer != "未知":
        parsed.buyer = extracted.buyer
    if extracted.seller and extracted.seller != "未知":
        parsed.seller = extracted.seller
    if extracted.item_summary and extracted.item_summary != "未知":
        parsed.item_summary = extracted.item_summary
    if extracted.invoice_type and extracted.invoice_type != "未知":
        parsed.invoice_type = extracted.invoice_type
    parsed.invoice_category = extracted.invoice_category.value
    extracted_currency = getattr(extracted, "currency", None)
    if extracted_currency:
        parsed.currency = normalize_currency(str(extracted_currency))

    if not parser_invoice_no_looks_valid and extracted.invoice_no:
        parsed.invoice_no = extracted.invoice_no
    if parsed.invoice_date is None and extracted.invoice_date:
        parsed.invoice_date = extracted.invoice_date
    if parsed.amount is None or amount_is_sentinel:
        if extracted.amount and extracted.amount >= Decimal("0.10"):
            parsed.amount = extracted.amount

    parsed.extraction_method = method_label
    parsed.confidence = max(extracted.confidence, parsed.confidence)
    return parsed.amount is not None and parsed.amount < Decimal("0.10")


def resolve_invoice_currency(
    parsed: ParsedInvoice,
    *,
    final_category: str,
    context_text: str = "",
) -> str:
    """Determine the final ISO-4217 currency code for an invoice."""
    if parsed.currency:
        return normalize_currency(parsed.currency)
    default_cur = "USD" if final_category == "overseas_invoice" else "CNY"
    combined = f"{context_text}\n{parsed.invoice_type or ''}\n{parsed.raw_text[:2000]}"
    return detect_currency(combined, default=default_cur)


async def enrich_and_validate_invoice(
    *,
    db: AsyncSession,
    ai: Any,
    settings: Settings,
    parsed: ParsedInvoice,
    payload: bytes,
    filename: str,
    email_context: str = "",
    allow_transport_relaxation: bool = True,
    scam_checker: Any = is_scam_text,
) -> IngestionVerdict:
    """Run LLM enrichment (with VLM fallback for image-only PDFs), anti-scam,
    VAT/category validation, and confidence gating on ``parsed`` in place."""
    parser_invoice_no_looks_valid = bool(
        parsed.invoice_no
        and parsed.invoice_no.isdigit()
        and len(parsed.invoice_no) in (8, 20)
    )
    strong_parse = (
        parsed.extraction_method in ("qr", "xml_xpath", "ofd_struct")
        or parser_invoice_no_looks_valid
    )

    should_enrich, amount_is_sentinel = needs_llm_enrichment(parsed)
    extracted: Any = None
    used_vlm = False

    if should_enrich:
        if parsed.raw_text and parsed.raw_text.strip():
            enriched_text = parsed.raw_text
            if email_context:
                enriched_text = (
                    f"[Email context]\n{email_context}\n[/Email context]\n\n"
                    f"[Attachment text]\n{parsed.raw_text}\n[/Attachment text]"
                )
            try:
                extracted = await ai.extract_invoice_fields(db, enriched_text)
            except Exception as exc:
                extracted = None
                logger.warning(
                    "LLM enrichment failed for %s (%s); falling back to parser result",
                    filename,
                    exc,
                )
        elif parsed.source_format == "pdf" and payload and hasattr(ai, "extract_invoice_fields_from_images"):
            images_b64 = await asyncio.to_thread(render_pdf_pages_to_base64_png, payload)
            if images_b64:
                try:
                    extracted = await ai.extract_invoice_fields_from_images(
                        db, images_b64, context_text=email_context
                    )
                    used_vlm = True
                except Exception as exc:
                    extracted = None
                    logger.warning(
                        "VLM fallback failed for image-only PDF %s (%s)",
                        filename,
                        exc,
                    )

        if extracted is not None and (
            extracted.is_valid_tax_invoice
            or extracted.invoice_category != InvoiceCategory.VAT_INVOICE
        ):
            amount_is_sentinel = merge_llm_into_parsed(
                parsed,
                extracted,
                method_label="vlm" if used_vlm else "llm",
            )

    scam_text = " ".join(s for s in (parsed.buyer, parsed.seller, parsed.item_summary) if s)
    scam_hit, scam_reason = scam_checker(scam_text)
    if scam_hit:
        return IngestionVerdict(
            accepted=False,
            rejection_outcome="scam_detected",
            extraction_log_outcome="not_vat_invoice",
            reason=f"scam signal: {scam_reason}",
            detail=f"Rejected: {scam_reason}",
        )

    final_type = parsed.invoice_type or ""
    type_is_valid = final_type in VALID_INVOICE_TYPES or any(
        vt in final_type for vt in VALID_INVOICE_TYPES if len(vt) > 3
    )
    transport_eticket = allow_transport_relaxation and is_transport_e_ticket(parsed)
    llm_rejected = (
        extracted is not None
        and not extracted.is_valid_tax_invoice
        and not strong_parse
        and not transport_eticket
    )
    heuristic_rejected = (
        extracted is None
        and not parsed.is_vat_document
        and not type_is_valid
        and not transport_eticket
    )
    final_category = (
        extracted.invoice_category.value
        if extracted is not None
        else (parsed.invoice_category or "vat_invoice")
    )
    if settings.STRICT_VAT_ONLY:
        should_reject = llm_rejected or (not type_is_valid and heuristic_rejected)
    else:
        should_reject = final_category == "vat_invoice" and (
            llm_rejected or (not type_is_valid and heuristic_rejected)
        )

    if should_reject:
        return IngestionVerdict(
            accepted=False,
            rejection_outcome="not_vat_invoice",
            extraction_log_outcome="not_vat_invoice",
            reason=f"type={final_type!r} category={final_category!r} llm_rejected={llm_rejected}",
            detail=(
                "File does not appear to be a valid VAT invoice "
                f"(type={final_type!r}, category={final_category!r}, llm_rejected={llm_rejected})"
            ),
            final_category=final_category,
        )

    amount_is_sentinel = parsed.amount is not None and parsed.amount < Decimal("0.10")
    effective_amount_is_sentinel = amount_is_sentinel and not transport_eticket
    effective_confidence_floor = 0.5 if transport_eticket else 0.6
    if (
        parsed.confidence < effective_confidence_floor
        or not parsed.invoice_no
        or effective_amount_is_sentinel
    ):
        reason = (
            "amount below sentinel"
            if effective_amount_is_sentinel
            else ("missing invoice_no" if not parsed.invoice_no else "low confidence")
        )
        return IngestionVerdict(
            accepted=False,
            rejection_outcome="low_confidence",
            extraction_log_outcome="low_confidence",
            reason=reason,
            detail=f"Extraction confidence too low (confidence={parsed.confidence:.2f}, reason={reason})",
            final_category=final_category,
        )

    parsed.seller = restore_legal_seller_name(parsed.seller, parsed.raw_text)
    final_currency = resolve_invoice_currency(
        parsed,
        final_category=final_category,
        context_text=email_context,
    )
    parsed.currency = final_currency
    return IngestionVerdict(
        accepted=True,
        final_category=final_category,
        final_currency=final_currency,
    )
