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

BULK_HEADERS = frozenset(["list-unsubscribe"])

SCAM_PHRASES = frozenset(
    [
        "代开",
        "代开发票",
        "代开各行业",
        "有发票出售",
        "出售发票",
        "提供发票",
        "发票业务",
        "联系微信",
        "微信号",
        "加微信",
        "加qq",
        "联系qq",
    ]
)

SCAM_CONTACT_PATTERN = re.compile(
    r"(微信|weixin|wechat|qq)[\s:：]*[a-zA-Z0-9_-]{5,20}",
    re.IGNORECASE,
)

SCAM_SPARSE_DIGITS_PATTERN = re.compile(
    r"(?:[\d][\s/\-_<>+*\.\|丨]{1,4}){4,}[\d]"
)


@dataclass
class ClassificationResult:
    is_invoice: bool
    tier: int
    confidence: float
    reason: str


def is_scam_text(text: str) -> tuple[bool, str]:
    if not text:
        return False, ""
    lowered = text.lower()
    for phrase in SCAM_PHRASES:
        if phrase in lowered:
            return True, f"scam phrase: {phrase}"
    if SCAM_CONTACT_PATTERN.search(text):
        return True, "wechat/qq contact inline"
    if SCAM_SPARSE_DIGITS_PATTERN.search(text):
        return True, "obfuscated phone/contact digits"
    return False, ""


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
        """Returns definitive False for obvious non-invoice, else None to pass to LLM."""
        headers_lower = {key.lower() for key in getattr(email, "headers", {}).keys()}

        if headers_lower & BULK_HEADERS:
            return ClassificationResult(False, 1, 0.98, "bulk header")

        scam_text = email.subject + " " + email.body_text[:2000]
        is_scam, scam_reason = is_scam_text(scam_text)
        if is_scam:
            return ClassificationResult(False, 1, 0.97, scam_reason)

        for attachment in email.attachments:
            ext = f'.{attachment.filename.rsplit(".", 1)[-1].lower()}' if "." in attachment.filename else ""
            if ext in INVOICE_EXTENSIONS:
                return ClassificationResult(True, 1, 0.95, f"invoice attachment: {attachment.filename}")

        if self._sender_trusted(email.from_addr):
            return ClassificationResult(True, 1, 0.98, "trusted sender")

        has_content = bool(email.attachments) or bool(email.body_links)
        has_keyword = self._has_keyword(email.subject + " " + email.body_text[:200])
        if not has_content and not has_keyword:
            return ClassificationResult(False, 1, 0.90, "no content or keywords")

        return None
