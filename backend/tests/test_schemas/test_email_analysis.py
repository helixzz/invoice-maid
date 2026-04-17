from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.invoice import EmailAnalysis, InvoiceFormat, InvoicePlatform, UrlKind


def test_email_analysis_accepts_invoice_with_download_url() -> None:
    result = EmailAnalysis(
        is_invoice_related=True,
        invoice_confidence=0.9,
        best_download_url="https://example.com/invoice.pdf",
        url_confidence=0.8,
        url_is_safelink=False,
        url_kind=UrlKind.DIRECT_FILE,
        extraction_hints={
            "platform": InvoicePlatform.NUONUO,
            "likely_formats": [InvoiceFormat.PDF, InvoiceFormat.XML],
        },
        skip_reason=None,
    )

    assert result.should_download is True


def test_email_analysis_requires_skip_reason_when_not_invoice() -> None:
    with pytest.raises(ValidationError, match="skip_reason required"):
        EmailAnalysis(is_invoice_related=False, invoice_confidence=0.2)


def test_email_analysis_rejects_url_fields_without_url() -> None:
    with pytest.raises(ValidationError, match="url_confidence must be 0 when no url"):
        EmailAnalysis(
            is_invoice_related=True,
            invoice_confidence=0.7,
            best_download_url=None,
            url_confidence=0.5,
            skip_reason=None,
        )


def test_email_analysis_rejects_skip_reason_when_invoice() -> None:
    with pytest.raises(ValidationError, match="skip_reason must be null"):
        EmailAnalysis(
            is_invoice_related=True,
            invoice_confidence=0.9,
            skip_reason="should not be here",
        )


def test_email_analysis_rejects_safelink_without_url() -> None:
    with pytest.raises(ValidationError, match="url_is_safelink must be false when no url"):
        EmailAnalysis(
            is_invoice_related=True,
            invoice_confidence=0.9,
            best_download_url=None,
            url_confidence=0.0,
            url_is_safelink=True,
        )


def test_email_analysis_rejects_url_kind_without_url() -> None:
    with pytest.raises(ValidationError, match="url_kind must be none when no url"):
        EmailAnalysis(
            is_invoice_related=True,
            invoice_confidence=0.9,
            best_download_url=None,
            url_confidence=0.0,
            url_is_safelink=False,
            url_kind=UrlKind.DIRECT_FILE,
        )


def test_email_analysis_rejects_best_url_when_not_invoice() -> None:
    with pytest.raises(ValidationError, match="best_download_url must be null when not invoice"):
        EmailAnalysis(
            is_invoice_related=False,
            invoice_confidence=0.1,
            best_download_url="https://example.com/invoice.pdf",
            url_confidence=0.9,
            url_kind=UrlKind.DIRECT_FILE,
            skip_reason="marketing",
        )
