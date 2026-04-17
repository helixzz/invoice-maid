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


def test_classify_tier1_positive_trusted_sender() -> None:
    classifier = EmailClassifier(trusted_senders=["billing@example.com"])

    result = classifier.classify_tier1(make_email(from_addr="Billing <billing@example.com>"))

    assert result is not None
    assert result.is_invoice is True
    assert result.tier == 1
    assert result.reason == "trusted sender"


def test_classify_tier1_positive_subject_keyword() -> None:
    classifier = EmailClassifier()

    result = classifier.classify_tier1(make_email(subject="您的发票已开具"))

    assert result is not None
    assert result.is_invoice is True
    assert result.reason == "invoice keyword in subject"


def test_classify_tier1_positive_invoice_attachment() -> None:
    classifier = EmailClassifier()
    email = make_email(
        attachments=[RawAttachment(filename="上海发票.pdf", payload=b"pdf", content_type="application/pdf")]
    )

    result = classifier.classify_tier1(email)

    assert result is not None
    assert result.is_invoice is True
    assert result.reason == "invoice attachment: 上海发票.pdf"


def test_classify_tier1_negative_bulk_header() -> None:
    classifier = EmailClassifier()

    result = classifier.classify_tier1(make_email(headers={"List-Unsubscribe": "mailto:test@example.com"}))

    assert result is not None
    assert result.is_invoice is False
    assert result.reason == "bulk header present"


def test_classify_tier1_negative_no_attachments_links_or_keywords() -> None:
    classifier = EmailClassifier()

    result = classifier.classify_tier1(make_email(body_text="plain chat"))

    assert result is not None
    assert result.is_invoice is False
    assert result.reason == "no attachments, links, or keywords"


def test_classify_tier1_returns_none_for_ambiguous_email() -> None:
    classifier = EmailClassifier()
    email = make_email(
        subject="Status update",
        body_text="See details in portal",
        body_links=["https://example.com/status"],
    )

    assert classifier.classify_tier1(email) is None


def test_classify_tier2_positive_pdf_mime_boost() -> None:
    classifier = EmailClassifier()
    email = make_email(
        subject="Status update",
        attachments=[RawAttachment(filename="doc.pdf", payload=b"pdf", content_type="application/pdf")],
    )

    result = classifier.classify_tier2(email)

    assert result is not None
    assert result.is_invoice is True
    assert result.tier == 2
    assert result.confidence == 0.7


def test_classify_tier2_stops_after_first_matching_attachment() -> None:
    classifier = EmailClassifier()
    email = make_email(
        attachments=[
            RawAttachment(filename="a.pdf", payload=b"a", content_type="application/pdf"),
            RawAttachment(filename="b.xml", payload=b"b", content_type="text/xml"),
        ]
    )

    result = classifier.classify_tier2(email)

    assert result is not None
    assert result.confidence == 0.7


def test_classify_tier2_positive_invoice_url_boost() -> None:
    classifier = EmailClassifier()
    email = make_email(
        subject="Portal message",
        body_text="Please visit the portal for your documents. Thanks." * 20,
        body_links=["https://etax.example.com/invoice/123"],
    )

    result = classifier.classify_tier2(email)

    assert result is not None
    assert result.is_invoice is True
    assert result.confidence == 0.7


def test_classify_tier2_positive_sender_and_body_keyword_boosts() -> None:
    classifier = EmailClassifier(trusted_senders=["finance@example.com"])
    email = make_email(
        from_addr="finance@example.com",
        body_text="这里附上报销信息，请查收。" * 50,
    )

    result = classifier.classify_tier2(email)

    assert result is not None
    assert result.is_invoice is True
    assert result.confidence == 0.75


def test_classify_tier2_negative_tiny_body_without_attachments() -> None:
    classifier = EmailClassifier()

    result = classifier.classify_tier2(make_email(body_text="tiny"))

    assert result is not None
    assert result.is_invoice is False
    assert result.confidence == 0.75


def test_classify_tier2_returns_none_for_ambiguous_score() -> None:
    classifier = EmailClassifier()

    assert classifier.classify_tier2(make_email(body_text="x" * 600)) is None


def test_build_llm_context_enriches_metadata() -> None:
    classifier = EmailClassifier()
    email = make_email(
        subject="Need review",
        body_text="Body text",
        from_addr="finance@example.com",
        attachments=[RawAttachment(filename="invoice.pdf", payload=b"pdf", content_type="application/pdf")],
        body_links=["https://example.com/a", "https://example.com/b"],
    )

    subject, body = classifier.build_llm_context(email)

    assert subject == "Need review"
    assert "From: finance@example.com" in body
    assert "Attachments: invoice.pdf" in body
    assert "Links: https://example.com/a, https://example.com/b" in body
    assert "Body:\nBody text" in body


def test_parse_helpers_normalize_entries() -> None:
    classifier = EmailClassifier(
        trusted_senders=["finance@example.com"],
        extra_keywords=["报税通知"],
    )

    assert classifier._sender_trusted("Finance <finance@example.com>") is True
    assert classifier._has_keyword("这是报税通知") is True
    assert _has_invoice_keyword("invoice attached") is True
    assert _has_invoice_keyword("friendly reminder") is False
