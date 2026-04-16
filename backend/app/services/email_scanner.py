# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false, reportUnannotatedClassAttribute=false, reportAny=false, reportExplicitAny=false, reportImplicitOverride=false, reportUnusedCallResult=false

from __future__ import annotations

import asyncio
import base64
import functools
import hashlib
import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Any, cast

import httpx
import msal
from cryptography.fernet import Fernet
from imap_tools import AND, MailBox, MailBoxPop3

from app.config import get_settings
from app.models import EmailAccount

logger = logging.getLogger(__name__)

URL_PATTERN = re.compile(r'https?://[^\s<>"\']+')
TAG_PATTERN = re.compile(r"<[^>]+>")
INVOICE_EXTENSIONS = {".pdf", ".xml", ".ofd"}
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


@dataclass
class RawAttachment:
    filename: str
    payload: bytes
    content_type: str


@dataclass
class RawEmail:
    uid: str
    subject: str
    body_text: str
    body_html: str
    from_addr: str
    received_at: datetime
    attachments: list[RawAttachment] = field(default_factory=list)
    body_links: list[str] = field(default_factory=list)


class BaseEmailScanner(ABC):
    @abstractmethod
    async def scan(self, account: EmailAccount, last_uid: str | None = None) -> list[RawEmail]:
        """Scan for new emails since last_uid. Returns list of new emails."""

    @abstractmethod
    async def test_connection(self, account: EmailAccount) -> bool:
        """Test if connection to email account is valid."""


def _derive_fernet_key(secret: str) -> bytes:
    """Derive a Fernet key from JWT_SECRET."""
    key_bytes = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(key_bytes)


def encrypt_password(plaintext: str, secret: str) -> str:
    fernet = Fernet(_derive_fernet_key(secret))
    return fernet.encrypt(plaintext.encode()).decode()


def decrypt_password(encrypted: str | None, secret: str | None = None) -> str:
    if not encrypted:
        return ""
    resolved_secret = secret or get_settings().JWT_SECRET
    fernet = Fernet(_derive_fernet_key(resolved_secret))
    return fernet.decrypt(encrypted.encode()).decode()


def _extract_urls(*body_parts: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for match in URL_PATTERN.findall(" ".join(body_parts)):
        if match not in seen:
            seen.add(match)
            urls.append(match)
    return urls


def _html_to_text(value: str) -> str:
    if not value:
        return ""
    without_tags = TAG_PATTERN.sub(" ", value)
    return re.sub(r"\s+", " ", unescape(without_tags)).strip()


def _normalize_datetime(value: datetime | str | None) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _is_uid_newer(candidate: str | None, previous: str | None) -> bool:
    if not candidate:
        return False
    if not previous:
        return True
    if candidate.isdigit() and previous.isdigit():
        return int(candidate) > int(previous)
    return candidate > previous


def _resolve_filename(name: str | None, fallback: str) -> str:
    cleaned = (name or "").strip()
    return cleaned or fallback


class ImapScanner(BaseEmailScanner):
    async def scan(self, account: EmailAccount, last_uid: str | None = None) -> list[RawEmail]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(self._scan_sync, account, last_uid))

    def _scan_sync(self, account: EmailAccount, last_uid: str | None) -> list[RawEmail]:
        password = decrypt_password(account.password_encrypted)
        emails: list[RawEmail] = []

        with MailBox(account.host or "", port=account.port or 993).login(account.username, password) as mailbox:
            criteria = AND(seen=False) if last_uid is None else AND()
            limit = 100 if last_uid is None else 200

            for msg in mailbox.fetch(criteria, limit=limit, reverse=True):
                if last_uid and not _is_uid_newer(getattr(msg, "uid", None), last_uid):
                    continue

                attachments: list[RawAttachment] = []
                for att in getattr(msg, "attachments", []):
                    filename = getattr(att, "filename", None)
                    ext = Path(filename or "").suffix.lower()
                    if ext not in INVOICE_EXTENSIONS:
                        continue
                    attachments.append(
                        RawAttachment(
                            filename=_resolve_filename(filename, f"attachment{ext or '.bin'}"),
                            payload=cast(bytes, getattr(att, "payload", b"")),
                            content_type=getattr(att, "content_type", None) or "application/octet-stream",
                        )
                    )

                body_text = cast(str, getattr(msg, "text", "") or "")
                body_html = cast(str, getattr(msg, "html", "") or "")
                emails.append(
                    RawEmail(
                        uid=cast(str, getattr(msg, "uid", "") or ""),
                        subject=cast(str, getattr(msg, "subject", "") or ""),
                        body_text=body_text,
                        body_html=body_html,
                        from_addr=cast(str, getattr(msg, "from_", "") or ""),
                        received_at=_normalize_datetime(getattr(msg, "date", None)),
                        attachments=attachments,
                        body_links=_extract_urls(body_text, body_html),
                    )
                )

        return emails

    async def test_connection(self, account: EmailAccount) -> bool:
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, functools.partial(self._test_sync, account))
        except Exception:
            logger.exception("IMAP connection test failed for account %s", account.id)
            return False

    def _test_sync(self, account: EmailAccount) -> bool:
        password = decrypt_password(account.password_encrypted)
        with MailBox(account.host or "", port=account.port or 993).login(account.username, password):
            return True


class Pop3Scanner(BaseEmailScanner):
    _MAX_RECENT_IDS = 1000

    async def scan(self, account: EmailAccount, last_uid: str | None = None) -> list[RawEmail]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(self._scan_sync, account, last_uid))

    def _scan_sync(self, account: EmailAccount, last_uid: str | None) -> list[RawEmail]:
        password = decrypt_password(account.password_encrypted)
        known_ids = self._load_recent_ids(last_uid)
        emails: list[RawEmail] = []

        with MailBoxPop3(account.host or "", port=account.port or 995).login(account.username, password) as mailbox:
            for index, msg in enumerate(mailbox.fetch(limit=200, reverse=True), start=1):
                message_id = self._message_id_for(msg, index)
                if message_id in known_ids:
                    continue

                attachments: list[RawAttachment] = []
                for att in getattr(msg, "attachments", []):
                    filename = getattr(att, "filename", None)
                    ext = Path(filename or "").suffix.lower()
                    if ext not in INVOICE_EXTENSIONS:
                        continue
                    attachments.append(
                        RawAttachment(
                            filename=_resolve_filename(filename, f"attachment{ext or '.bin'}"),
                            payload=cast(bytes, getattr(att, "payload", b"")),
                            content_type=getattr(att, "content_type", None) or "application/octet-stream",
                        )
                    )

                body_text = cast(str, getattr(msg, "text", "") or "")
                body_html = cast(str, getattr(msg, "html", "") or "")
                emails.append(
                    RawEmail(
                        uid=message_id,
                        subject=cast(str, getattr(msg, "subject", "") or ""),
                        body_text=body_text,
                        body_html=body_html,
                        from_addr=cast(str, getattr(msg, "from_", "") or ""),
                        received_at=_normalize_datetime(getattr(msg, "date", None)),
                        attachments=attachments,
                        body_links=_extract_urls(body_text, body_html),
                    )
                )

        return emails

    async def test_connection(self, account: EmailAccount) -> bool:
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, functools.partial(self._test_sync, account))
        except Exception:
            logger.exception("POP3 connection test failed for account %s", account.id)
            return False

    def _test_sync(self, account: EmailAccount) -> bool:
        password = decrypt_password(account.password_encrypted)
        with MailBoxPop3(account.host or "", port=account.port or 995).login(account.username, password):
            return True

    def _load_recent_ids(self, raw_value: str | None) -> set[str]:
        if not raw_value:
            return set()
        try:
            decoded = json.loads(raw_value)
        except json.JSONDecodeError:
            return {raw_value}
        if not isinstance(decoded, list):
            return set()
        return {str(item) for item in decoded if item}

    def serialize_recent_ids(self, ids: list[str]) -> str:
        return json.dumps(ids[: self._MAX_RECENT_IDS], ensure_ascii=False)

    def _message_id_for(self, msg: Any, index: int) -> str:
        headers = getattr(msg, "headers", None)
        message_id = ""

        if headers is not None:
            try:
                if hasattr(headers, "get"):
                    header_value = headers.get("Message-ID") or headers.get("Message-Id")
                else:
                    header_value = headers["Message-ID"]
            except Exception:
                header_value = None

            if isinstance(header_value, list):
                message_id = str(header_value[0]) if header_value else ""
            elif header_value is not None:
                message_id = str(header_value)

        if message_id:
            return message_id.strip()

        fallback_bits = [
            cast(str, getattr(msg, "subject", "") or ""),
            cast(str, getattr(msg, "from_", "") or ""),
            str(getattr(msg, "date", "") or ""),
            str(index),
        ]
        digest = hashlib.sha256("|".join(fallback_bits).encode()).hexdigest()
        return f"pop3-{digest}"


class OutlookScanner(BaseEmailScanner):
    SCOPES = ["Mail.Read"]

    async def scan(self, account: EmailAccount, last_uid: str | None = None) -> list[RawEmail]:
        access_token = await self._acquire_access_token(account)
        headers = {"Authorization": f"Bearer {access_token}"}
        params: dict[str, str] = {
            "$top": "100",
            "$orderby": "receivedDateTime desc",
            "$select": "id,internetMessageId,subject,body,from,receivedDateTime,hasAttachments",
        }
        if last_uid is None:
            params["$filter"] = self._recent_message_filter()

        emails: list[RawEmail] = []
        next_url = f"{GRAPH_BASE_URL}/me/mailFolders/inbox/messages"

        async with httpx.AsyncClient(timeout=30.0) as client:
            while next_url and len(emails) < 200:
                response = await client.get(next_url, headers=headers, params=params if next_url.endswith("/messages") else None)
                response.raise_for_status()
                payload = response.json()

                for item in payload.get("value", []):
                    message_uid = cast(str, item.get("internetMessageId") or item.get("id") or "")
                    if last_uid and message_uid == last_uid:
                        return emails

                    body = cast(dict[str, Any], item.get("body") or {})
                    body_type = str(body.get("contentType") or "").lower()
                    raw_body = str(body.get("content") or "")
                    body_text = raw_body if body_type == "text" else _html_to_text(raw_body)
                    body_html = raw_body if body_type == "html" else ""
                    attachments = await self._fetch_attachments(client, headers, cast(str, item.get("id") or ""))
                    sender = cast(dict[str, Any], item.get("from") or {})
                    email_address = cast(dict[str, Any], sender.get("emailAddress") or {})

                    emails.append(
                        RawEmail(
                            uid=message_uid,
                            subject=str(item.get("subject") or ""),
                            body_text=body_text,
                            body_html=body_html,
                            from_addr=str(email_address.get("address") or ""),
                            received_at=_normalize_datetime(item.get("receivedDateTime")),
                            attachments=attachments,
                            body_links=_extract_urls(body_text, body_html),
                        )
                    )

                    if len(emails) >= 200:
                        break

                next_url = cast(str | None, payload.get("@odata.nextLink"))
                params = {}

        return emails

    async def test_connection(self, account: EmailAccount) -> bool:
        try:
            access_token = await self._acquire_access_token(account)
            headers = {"Authorization": f"Bearer {access_token}"}
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    f"{GRAPH_BASE_URL}/me/mailFolders/inbox/messages",
                    headers=headers,
                    params={"$top": "1", "$select": "id"},
                )
                response.raise_for_status()
            return True
        except Exception:
            logger.exception("Outlook connection test failed for account %s", account.id)
            return False

    async def _acquire_access_token(self, account: EmailAccount) -> str:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, functools.partial(self._acquire_token_sync, account))
        access_token = result.get("access_token")
        if not access_token:
            raise RuntimeError(f"Outlook auth failed: {result.get('error_description', 'unknown')}")
        return str(access_token)

    def _acquire_token_sync(self, account: EmailAccount) -> dict[str, Any]:
        token_cache = self._load_cache(account)
        app = msal.PublicClientApplication(
            client_id=account.username,
            authority="https://login.microsoftonline.com/common",
            token_cache=token_cache,
        )

        result: dict[str, Any] | None = None
        accounts = app.get_accounts()
        if accounts:
            result = cast(dict[str, Any] | None, app.acquire_token_silent(self.SCOPES, account=accounts[0]))

        if not result:
            flow = app.initiate_device_flow(scopes=self.SCOPES)
            if "user_code" not in flow:
                raise RuntimeError("Failed to start Outlook device flow")
            logger.info(
                "Outlook auth required. Go to %s and enter code: %s",
                flow["verification_uri"],
                flow["user_code"],
            )
            result = cast(dict[str, Any], app.acquire_token_by_device_flow(flow))

        self._save_cache(account, token_cache)
        return result or {}

    async def _fetch_attachments(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        message_id: str,
    ) -> list[RawAttachment]:
        if not message_id:
            return []

        response = await client.get(
            f"{GRAPH_BASE_URL}/me/messages/{message_id}/attachments",
            headers=headers,
            params={"$top": "50"},
        )
        response.raise_for_status()

        attachments: list[RawAttachment] = []
        for item in response.json().get("value", []):
            if item.get("@odata.type") != "#microsoft.graph.fileAttachment":
                continue
            filename = _resolve_filename(cast(str | None, item.get("name")), "attachment.bin")
            ext = Path(filename).suffix.lower()
            if ext not in INVOICE_EXTENSIONS:
                continue
            payload_base64 = item.get("contentBytes")
            if not isinstance(payload_base64, str):
                continue
            attachments.append(
                RawAttachment(
                    filename=filename,
                    payload=base64.b64decode(payload_base64),
                    content_type=str(item.get("contentType") or "application/octet-stream"),
                )
            )
        return attachments

    def _load_cache(self, account: EmailAccount) -> msal.SerializableTokenCache:
        cache = msal.SerializableTokenCache()
        token_path = account.oauth_token_path
        if not token_path:
            return cache
        try:
            cache.deserialize(Path(token_path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cache
        return cache

    def _save_cache(self, account: EmailAccount, cache: msal.SerializableTokenCache) -> None:
        token_path = account.oauth_token_path
        if not token_path or not cache.has_state_changed:
            return
        path = Path(token_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(cache.serialize(), encoding="utf-8")

    def _recent_message_filter(self) -> str:
        since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat().replace("+00:00", "Z")
        return f"receivedDateTime ge {since}"


class ScannerFactory:
    @staticmethod
    def get_scanner(account_type: str) -> BaseEmailScanner:
        scanners: dict[str, BaseEmailScanner] = {
            "imap": ImapScanner(),
            "pop3": Pop3Scanner(),
            "outlook": OutlookScanner(),
            "qq": ImapScanner(),
        }
        scanner = scanners.get(account_type)
        if scanner is None:
            raise ValueError(f"Unknown email account type: {account_type}")
        return scanner


__all__ = [
    "BaseEmailScanner",
    "ImapScanner",
    "OutlookScanner",
    "Pop3Scanner",
    "RawAttachment",
    "RawEmail",
    "ScannerFactory",
    "decrypt_password",
    "encrypt_password",
]
