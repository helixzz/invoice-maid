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


def test_is_scam_text_detects_all_patterns() -> None:
    from app.services.email_classifier import is_scam_text

    assert is_scam_text("") == (False, "")
    assert is_scam_text("regular invoice") == (False, "")

    flag, reason = is_scam_text("代开发票联系我")
    assert flag is True
    assert "代开" in reason

    flag, reason = is_scam_text("please add 微信: abc12345 for invoices")
    assert flag is True
    assert "wechat" in reason.lower()

    flag, reason = is_scam_text("price 0 3 - 6 1 9 < + 2 - - 4 8 5 8 contact")
    assert flag is True
    assert "obfuscated" in reason


def test_classify_tier1_rejects_scam_subject_and_body() -> None:
    classifier = EmailClassifier()
    email = make_email(
        subject="代开各行业发票联系微信gn81186",
        body_text="密 0 3 5 0 4 4 / - + * 8 2 1 3 5",
        attachments=[RawAttachment(filename="invoice.pdf", content_type="application/pdf", payload=b"pdf")],
    )
    result = classifier.classify_tier1(email)
    assert result is not None
    assert result.is_invoice is False
    assert "scam phrase" in result.reason


def test_tier1_keyword_matches_new_chinese_phrases_v091() -> None:
    """v0.9.1 added 7 Chinese keywords to INVOICE_KEYWORDS. Previously a
    email with subject '山姆会员商店购物发票' and no attachments/links
    would fall through tier-1 as 'no content or keywords'; now '购物发票'
    is in the keyword set."""
    classifier = EmailClassifier()
    for subject in (
        "山姆会员商店购物发票",
        "您的购物凭证已生成",
        "消费凭证 #12345",
        "京东订单完成通知",
        "Alipay 交易成功",
        "支付凭证",
        "貴公司的發票",
    ):
        email = make_email(subject=subject, body_text="", attachments=[], body_links=[])
        result = classifier.classify_tier1(email)
        assert result is None, (
            f"subject {subject!r} should pass tier-1 to LLM (None), "
            f"got {result!r}"
        )


def test_tier1_body_scan_covers_2000_chars_not_200() -> None:
    """v0.9.1 expanded the tier-1 body keyword window from 200 to 2000
    chars. An invoice keyword buried at position ~500 now triggers a
    pass-to-LLM instead of an instant 'no content or keywords' reject."""
    classifier = EmailClassifier()
    filler = "x" * 500
    body_with_keyword_beyond_200 = f"{filler}发票{filler}"
    email = make_email(
        subject="neutral",
        body_text=body_with_keyword_beyond_200,
        attachments=[],
        body_links=[],
    )
    result = classifier.classify_tier1(email)
    assert result is None


def test_tier1_body_scan_still_bounded_at_2000_chars() -> None:
    """The body-scan window is still bounded (performance); keywords at
    position 5000+ don't trigger tier-1 and the email falls through to
    the 'no content or keywords' path."""
    classifier = EmailClassifier()
    filler = "x" * 5000
    body_with_keyword_beyond_2000 = f"{filler}发票"
    email = make_email(
        subject="neutral",
        body_text=body_with_keyword_beyond_2000,
        attachments=[],
        body_links=[],
    )
    result = classifier.classify_tier1(email)
    assert result is not None
    assert result.is_invoice is False


def test_trusted_sender_exact_email_rejects_spoofed_substring() -> None:
    """v0.9.1 security fix: pre-v0.9.1 substring match would trust
    ``notbilling@company.com`` if ``billing@company.com`` was the
    configured trusted sender. Exact-email mode closes that hole."""
    classifier = EmailClassifier(trusted_senders=["billing@company.com"])
    legitimate = make_email(from_addr="billing@company.com")
    spoofed = make_email(from_addr="notbilling@company.com")
    assert classifier._sender_trusted(legitimate.from_addr) is True
    assert classifier._sender_trusted(spoofed.from_addr) is False


def test_trusted_sender_domain_pattern_matches_all_mailboxes_on_domain() -> None:
    """``@qcloudmail.com`` trusts every mailbox on that domain; bare
    ``qcloudmail.com`` is equivalent (domain-suffix form)."""
    classifier = EmailClassifier(
        trusted_senders=["@qcloudmail.com", "jd.com"]
    )
    assert classifier._sender_trusted("noreply@qcloudmail.com") is True
    assert classifier._sender_trusted("bff407d2@qcloudmail.com") is True
    assert classifier._sender_trusted("fapiao@jd.com") is True
    assert classifier._sender_trusted("order@sub.jd.com") is True
    assert classifier._sender_trusted("evil@qcloudmail-evil.com") is False


def test_trusted_sender_handles_display_name_brackets() -> None:
    """Real email headers often come as ``Display Name <mailbox@domain>``.
    The trusted-sender check must extract the mailbox before comparing."""
    classifier = EmailClassifier(trusted_senders=["billing@company.com"])
    assert classifier._sender_trusted("Billing Dept <billing@company.com>") is True
    assert classifier._sender_trusted("Legit <notbilling@company.com>") is False


def test_trusted_sender_legacy_substring_still_works() -> None:
    """Backwards compat: operator-configured patterns that aren't a
    full email or domain still match via substring, so pre-v0.9.1
    instance settings keep working after upgrade."""
    classifier = EmailClassifier(trusted_senders=["invoicebot"])
    assert classifier._sender_trusted("invoicebot@anywhere.example") is True
    assert classifier._sender_trusted("support@example.com") is False


def test_trusted_sender_empty_and_whitespace_patterns_ignored() -> None:
    """Empty string, whitespace-only entries in the configured list
    are silently skipped (not treated as 'match everything')."""
    classifier = EmailClassifier(trusted_senders=["", "   ", "billing@company.com"])
    assert classifier._sender_trusted("random@nowhere.com") is False
    assert classifier._sender_trusted("billing@company.com") is True


def test_trusted_sender_handles_malformed_brackets() -> None:
    """Edge case: a from_addr like ``a> <b`` has '<' and '>' but
    backwards. The mailbox-extraction logic must fall back to the
    whole string (no exception, no accidental match on garbage)."""
    classifier = EmailClassifier(trusted_senders=["billing@company.com"])
    assert classifier._sender_trusted("billing@company.com> <garbage") is False
    assert classifier._sender_trusted("billing@company.com") is True
