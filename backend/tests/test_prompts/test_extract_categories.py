"""Prompt-validation against the 5 category fixtures.

Hits the real LLM via ``AIService.extract_invoice_fields``. Skipped by
default so CI doesn't depend on provider availability; run explicitly
with ``INVOICE_MAID_LIVE_LLM_TESTS=1``.

Uses the actual public APIs:
  - app.services.invoice_parser.parse(filename, content) -> ParsedInvoice
  - app.services.ai_service.AIService.extract_invoice_fields(db, text) -> InvoiceExtract
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.services import invoice_parser
from app.services.ai_service import AIService


LIVE = os.getenv("INVOICE_MAID_LIVE_LLM_TESTS") == "1"
requires_live_llm = pytest.mark.skipif(
    not LIVE, reason="Live LLM test; set INVOICE_MAID_LIVE_LLM_TESTS=1"
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "invoice_samples"


@requires_live_llm
@pytest.mark.parametrize(
    "fixture_rel,expected_category,expected_valid_tax",
    [
        ("vat_invoice/synthetic_vat.pdf", "vat_invoice", True),
        ("overseas_invoice/cursor_sample.pdf", "overseas_invoice", False),
        ("receipt/synthetic_receipt.pdf", "receipt", False),
        ("proforma/synthetic_proforma.pdf", "proforma", False),
        ("other/synthetic_other.pdf", "other", False),
    ],
)
async def test_extract_categories(
    fixture_rel: str,
    expected_category: str,
    expected_valid_tax: bool,
    settings,
    db,
) -> None:
    path = FIXTURES / fixture_rel
    assert path.exists(), (
        f"fixture missing: {path}; "
        "run tests/fixtures/generate_category_samples.py first"
    )
    parsed = invoice_parser.parse(path.name, path.read_bytes())
    ai = AIService(settings)
    extracted = await ai.extract_invoice_fields(db, parsed.raw_text)
    assert extracted.invoice_category.value == expected_category, (
        f"category mismatch for {fixture_rel}: "
        f"got {extracted.invoice_category.value}"
    )
    assert extracted.is_valid_tax_invoice is expected_valid_tax, (
        f"is_valid_tax_invoice mismatch for {fixture_rel}: "
        f"got {extracted.is_valid_tax_invoice}, expected {expected_valid_tax}"
    )


def test_fixture_generator_can_be_imported() -> None:
    """Structural guard: the fixture generator must remain importable.
    We skip the live-LLM tests by default but still verify the generator
    module is syntactically valid so a CI PR that renames reportlab
    symbols can't silently regress the generator."""
    from tests.fixtures import generate_category_samples

    assert callable(generate_category_samples.generate_all)
    assert callable(generate_category_samples.make_pdf)
