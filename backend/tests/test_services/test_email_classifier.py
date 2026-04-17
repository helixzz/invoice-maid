from __future__ import annotations

from datetime import datetime, timezone

from app.services.email_classifier import EmailClassifier, _has_invoice_keyword
from app.services.email_scanner import RawAttachment, RawEmail


def make_email(**overrides) -> RawEmail:
    defaults = {
        "uid": "uid-1",
        "subject": "Hello",
        "body_text": "regular body",
        "body_html": "",
        "from_addr": "sender@example.com",
        "received_at": datetime.now(timezone.utc),
        "attachments": [],
        "body_links": [],
        "headers": {},
    }
    defaults.update(overrides)
    return RawEmail(**defaults)


def test_classify_tier1_negative_bulk_header_wins_over_attachment() -> None:
    classifier = EmailClassifier()
    email = make_email(
        headers={"List-Unsubscribe": "mailto:test@example.com"},
        attachments=[RawAttachment(filename="invoice.pdf", payload=b"pdf", content_type="application/pdf")],
    )

    result = classifier.classify_tier1(email)

    assert result is not None
    assert result.is_invoice is False
    assert result.reason == "bulk header"


def test_classify_tier1_positive_invoice_attachment_without_keyword() -> None:
    classifier = EmailClassifier()
    email = make_email(
        attachments=[RawAttachment(filename="statement.pdf", payload=b"pdf", content_type="application/pdf")]
    )

    result = classifier.classify_tier1(email)

    assert result is not None
    assert result.is_invoice is True
    assert result.tier == 1
    assert result.confidence == 0.95
    assert result.reason == "invoice attachment: statement.pdf"


def test_classify_tier1_positive_trusted_sender_without_attachment() -> None:
    classifier = EmailClassifier(trusted_senders=["billing@example.com"])

    result = classifier.classify_tier1(
        make_email(from_addr="Billing <billing@example.com>", body_links=["https://example.com/portal"])
    )

    assert result is not None
    assert result.is_invoice is True
    assert result.reason == "trusted sender"


def test_classify_tier1_ignores_precedence_header_only() -> None:
    classifier = EmailClassifier()

    result = classifier.classify_tier1(make_email(headers={"Precedence": "bulk"}, body_text="invoice portal", body_links=["https://example.com"] ))

    assert result is None


def test_classify_tier1_negative_no_content_or_keywords() -> None:
    classifier = EmailClassifier()

    result = classifier.classify_tier1(make_email(body_text="plain chat"))

    assert result is not None
    assert result.is_invoice is False
    assert result.reason == "no content or keywords"


def test_classify_tier1_returns_none_for_ambiguous_email() -> None:
    classifier = EmailClassifier()
    email = make_email(
        subject="Status update",
        body_text="See details in portal",
        body_links=["https://example.com/status"],
    )

    assert classifier.classify_tier1(email) is None


def test_parse_helpers_normalize_entries() -> None:
    classifier = EmailClassifier(
        trusted_senders=["finance@example.com"],
        extra_keywords=["报税通知"],
    )

    assert classifier._sender_trusted("Finance <finance@example.com>") is True
    assert classifier._has_keyword("这是报税通知") is True
    assert _has_invoice_keyword("invoice attached") is True
    assert _has_invoice_keyword("friendly reminder") is False
