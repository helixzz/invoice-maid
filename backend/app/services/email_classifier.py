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

DEFAULT_BLOCKED_SENDERS = frozenset(
    [
        "notifications@github.com",
        "noreply@github.com",
        "no-reply@github.com",
        "notifications@gitlab.com",
        "builds@travis-ci.org",
    ]
)

DEFAULT_NOISE_SUBJECT_SUBSTRINGS = frozenset(
    [
        "pr run failed:",
        "run failed:",
        "active backup for business",
        "workflow run failed",
    ]
)

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
    return [sender.strip().lower() for sender in re.split(r"[,\n]", raw) if sender.strip()]


def _parse_blocked_senders(raw: str) -> list[str]:
    return [sender.strip().lower() for sender in re.split(r"[,\n]", raw) if sender.strip()]


def _parse_extra_keywords(raw: str) -> list[str]:
    return [keyword.strip().lower() for keyword in re.split(r"[,\n]", raw) if keyword.strip()]


def _match_sender_patterns(from_addr: str, patterns: set[str]) -> bool:
    lowered = from_addr.lower()
    mailbox = lowered
    if "<" in lowered and ">" in lowered:
        start = lowered.rfind("<") + 1
        end = lowered.rfind(">")
        if end > start:
            mailbox = lowered[start:end]
    domain = mailbox.rsplit("@", 1)[-1] if "@" in mailbox else ""

    for raw_pattern in patterns:
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


class EmailClassifier:
    def __init__(
        self,
        trusted_senders: list[str] | None = None,
        extra_keywords: list[str] | None = None,
        blocked_senders: list[str] | None = None,
    ) -> None:
        self._trusted_senders = set(trusted_senders or [])
        self._extra_keywords = set(extra_keywords or [])
        self._blocked_senders = set(DEFAULT_BLOCKED_SENDERS) | set(blocked_senders or [])
        self._all_keywords = INVOICE_KEYWORDS | self._extra_keywords

    def _sender_trusted(self, from_addr: str) -> bool:
        """Match trusted-sender patterns against the full from_addr."""
        return _match_sender_patterns(from_addr, self._trusted_senders)

    def _sender_blocked(self, from_addr: str) -> bool:
        """Match blocked/noise sender patterns against the full from_addr."""
        return _match_sender_patterns(from_addr, self._blocked_senders)

    def _has_keyword(self, text: str) -> bool:
        lowered = text.lower()
        return any(keyword in lowered for keyword in self._all_keywords)

    def _is_noise_subject(self, subject: str) -> bool:
        lowered = (subject or "").lower()
        return any(pat in lowered for pat in DEFAULT_NOISE_SUBJECT_SUBSTRINGS)

    def classify_metadata_preflight(self, email: RawEmail) -> ClassificationResult | None:
        """Fast pre-hydration filter on email metadata (subject, sender, preview).

        Avoids triggering expensive Outlook Graph / IMAP body hydration on obvious
        non-invoice notifications (e.g. GitHub CI failure emails, NAS backup alerts,
        scam mail, or user-blocked senders) when no attachments are present.
        """
        headers = getattr(email, "headers", {}) or {}
        headers_lower = {key.lower() for key in headers.keys()}
        if headers_lower & BULK_HEADERS:
            return ClassificationResult(False, 1, 0.98, "bulk header")

        preview_text = (email.subject or "") + " " + (email.body_text or "")[:1000]
        is_scam, scam_reason = is_scam_text(preview_text)
        if is_scam:
            return ClassificationResult(False, 1, 0.97, scam_reason)

        if self._sender_trusted(email.from_addr):
            return None

        has_attachments = bool(email.attachments) or headers.get("_has_attachments") == "True"
        if not has_attachments and not self._has_keyword(preview_text):
            if self._sender_blocked(email.from_addr):
                return ClassificationResult(False, 1, 0.96, "blocked sender")
            if self._is_noise_subject(email.subject):
                return ClassificationResult(False, 1, 0.95, "noise subject")

        return None

    def classify_tier1(self, email: RawEmail) -> ClassificationResult | None:
        """Returns definitive True/False for obvious cases, else None to pass to LLM."""
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

        has_keyword = self._has_keyword(email.subject + " " + email.body_text[:2000])
        if not email.attachments and not has_keyword:
            if self._sender_blocked(email.from_addr):
                return ClassificationResult(False, 1, 0.96, "blocked sender")
            if self._is_noise_subject(email.subject):
                return ClassificationResult(False, 1, 0.95, "noise subject")

        has_content = bool(email.attachments) or bool(email.body_links)
        if not has_content and not has_keyword:
            return ClassificationResult(False, 1, 0.90, "no content or keywords")

        return None
