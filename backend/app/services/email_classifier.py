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
        "购物凭证",
        "消费凭证",
        "购物发票",
        "订单完成",
        "交易成功",
        "支付凭证",
        "發票",
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
        """Match trusted-sender patterns against the full from_addr.

        Three accepted pattern shapes:

        * ``full.email@domain.com`` — exact-email match against the parsed
          mailbox part, so ``notbilling@domain.com`` cannot impersonate a
          configured ``billing@domain.com`` trusted sender via substring.
        * ``@domain.com`` / ``domain.com`` — domain-suffix match against
          the mailbox's domain part; trusts every mailbox on that domain.
        * Other strings — legacy substring fallback (unchanged behaviour
          for trusted-sender entries that predate v0.9.1); kept so existing
          instance settings don't silently stop matching after upgrade.
        """
        lowered = from_addr.lower()
        mailbox = lowered
        if "<" in lowered and ">" in lowered:
            start = lowered.rfind("<") + 1
            end = lowered.rfind(">")
            if end > start:
                mailbox = lowered[start:end]
        domain = mailbox.rsplit("@", 1)[-1] if "@" in mailbox else ""

        for raw_pattern in self._trusted_senders:
            pattern = raw_pattern.strip().lower()
            if not pattern:
                continue
            if "@" in pattern and not pattern.startswith("@"):
                if pattern == mailbox:
                    return True
                continue
            if pattern.startswith("@"):
                if domain == pattern[1:] or domain.endswith("." + pattern[1:]):
                    return True
                continue
            if domain and (domain == pattern or domain.endswith("." + pattern)):
                return True
            if pattern in lowered:
                return True
        return False

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
        has_keyword = self._has_keyword(email.subject + " " + email.body_text[:2000])
        if not has_content and not has_keyword:
            return ClassificationResult(False, 1, 0.90, "no content or keywords")

        return None
