# pyright: reportUnusedFunction=false

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from app.services.email_scanner import RawEmail

INVOICE_KEYWORDS = frozenset(
    [
        "发票",
        "invoice",
        "开票",
        "报销",
        "税务",
        "fapiao",
        "receipt",
        "vat",
        "增值税",
        "电子发票",
        "数电票",
    ]
)

INVOICE_EXTENSIONS = frozenset([".pdf", ".xml", ".ofd"])

INVOICE_MIME_TYPES = frozenset(
    [
        "application/pdf",
        "text/xml",
        "application/xml",
        "application/ofd",
    ]
)

INVOICE_URL_PATTERNS = re.compile(
    r"(einvoice|etax|fapiao|invoice|发票|税务|baiwang|aisino|nuonuo)",
    re.IGNORECASE,
)

BULK_HEADERS = frozenset(["list-unsubscribe", "precedence"])


@dataclass
class ClassificationResult:
    is_invoice: bool
    tier: int
    confidence: float
    reason: str


def _has_invoice_keyword(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in INVOICE_KEYWORDS)


def _parse_trusted_senders(raw: str) -> list[str]:
    return [sender.strip().lower() for sender in raw.split(",") if sender.strip()]


def _parse_extra_keywords(raw: str) -> list[str]:
    return [keyword.strip().lower() for keyword in raw.split(",") if keyword.strip()]


class EmailClassifier:
    def __init__(
        self,
        trusted_senders: list[str] | None = None,
        extra_keywords: list[str] | None = None,
    ) -> None:
        self._trusted_senders = set(trusted_senders or [])
        self._extra_keywords = set(extra_keywords or [])
        self._all_keywords = INVOICE_KEYWORDS | self._extra_keywords

    def _sender_trusted(self, from_addr: str) -> bool:
        lowered = from_addr.lower()
        return any(sender in lowered for sender in self._trusted_senders)

    def _has_keyword(self, text: str) -> bool:
        lowered = text.lower()
        return any(keyword in lowered for keyword in self._all_keywords)

    def classify_tier1(self, email: RawEmail) -> ClassificationResult | None:
        """Return a definitive result or None for escalation."""
        headers_lower = {key.lower() for key in email.headers}

        if headers_lower & BULK_HEADERS:
            return ClassificationResult(False, 1, 0.95, "bulk header present")

        if self._sender_trusted(email.from_addr):
            return ClassificationResult(True, 1, 0.98, "trusted sender")

        for attachment in email.attachments:
            extension = (
                f'.{attachment.filename.rsplit(".", 1)[-1].lower()}'
                if "." in attachment.filename
                else ""
            )
            if extension in INVOICE_EXTENSIONS and self._has_keyword(attachment.filename):
                return ClassificationResult(True, 1, 0.97, f"invoice attachment: {attachment.filename}")

        if self._has_keyword(email.subject):
            return ClassificationResult(True, 1, 0.90, "invoice keyword in subject")

        has_content = bool(email.attachments) or bool(email.body_links)
        body_has_keyword = self._has_keyword(email.body_text[:500])
        if not has_content and not body_has_keyword:
            return ClassificationResult(False, 1, 0.85, "no attachments, links, or keywords")

        return None

    def classify_tier2(self, email: RawEmail) -> ClassificationResult | None:
        """Return a cheap metadata-based result or None for LLM fallback."""
        score = 0.5

        for attachment in email.attachments:
            if attachment.content_type.lower() in INVOICE_MIME_TYPES:  # pragma: no branch
                score += 0.2
                break

        combined_links = " ".join(email.body_links[:10])
        if INVOICE_URL_PATTERNS.search(combined_links):
            score += 0.2

        if self._sender_trusted(email.from_addr):
            score += 0.1

        if self._has_keyword(email.body_text[:1000]):
            score += 0.15

        if len(email.body_text) < 500 and not email.attachments:
            score -= 0.25

        score = max(0.0, min(1.0, score))

        if score > 0.6:
            return ClassificationResult(True, 2, score, f"tier2 score={score:.2f}")
        if score < 0.3:
            return ClassificationResult(False, 2, 1.0 - score, f"tier2 score={score:.2f}")

        return None

    def build_llm_context(self, email: RawEmail) -> tuple[str, str]:
        attachment_names = ", ".join(attachment.filename for attachment in email.attachments) or "none"
        links = ", ".join(email.body_links[:5]) or "none"
        enriched_body = (
            f"From: {email.from_addr}\n"
            f"Attachments: {attachment_names}\n"
            f"Links: {links}\n\n"
            f"Body:\n{email.body_text[:2000]}"
        )
        return email.subject, enriched_body
