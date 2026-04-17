# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false, reportUnannotatedClassAttribute=false, reportAny=false, reportExplicitAny=false, reportImplicitOverride=false, reportUnusedCallResult=false

from __future__ import annotations

import asyncio
import base64
import email
from email import policy
from email.utils import parsedate_to_datetime
import functools
import hashlib
import imaplib
import json
import logging
import poplib
import re
import ssl
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Any, cast

import httpx
import msal
from msal.exceptions import MsalServiceError
from cryptography.fernet import Fernet
from imap_tools import AND, MailBox
from imap_tools.errors import MailboxLoginError

from app.config import get_settings
from app.models import EmailAccount

logger = logging.getLogger(__name__)

IMAP_CONNECTION_ERRORS = (OSError, ssl.SSLError, imaplib.IMAP4.error, MailboxLoginError)
POP3_CONNECTION_ERRORS = (OSError, ssl.SSLError, poplib.error_proto)
OUTLOOK_CONNECTION_ERRORS = (OSError, httpx.HTTPError, MsalServiceError)

URL_PATTERN = re.compile(r'https?://[^\s<>"\']+')
TAG_PATTERN = re.compile(r"<[^>]+>")
INVOICE_EXTENSIONS = {".pdf", ".xml", ".ofd"}
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


@dataclass
class OAuthFlowState:
    status: str
    verification_uri: str = ""
    user_code: str = ""
    expires_at: datetime | None = None
    detail: str | None = None
    task: asyncio.Task[Any] | None = field(default=None, repr=False)


class OAuthFlowRegistry:
    def __init__(self) -> None:
        self._flows: dict[int, OAuthFlowState] = {}

    def get(self, account_id: int) -> OAuthFlowState | None:
        state = self._flows.get(account_id)
        if state and state.expires_at and datetime.now(timezone.utc) > state.expires_at:
            state.status = "expired"
            state.detail = "Device code expired"
            if state.task and not state.task.done():
                state.task.cancel()
        return state

    def set(self, account_id: int, state: OAuthFlowState) -> None:
        self._flows[account_id] = state

    def remove(self, account_id: int) -> None:
        state = self._flows.pop(account_id, None)
        if state and state.task and not state.task.done():
            state.task.cancel()


oauth_registry = OAuthFlowRegistry()


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
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = parsedate_to_datetime(value)
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
        except IMAP_CONNECTION_ERRORS as exc:
            logger.exception("IMAP connection test failed for account %s: %s", account.id, exc)
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

        mailbox = poplib.POP3_SSL(account.host or "", account.port or 995)
        try:
            mailbox.user(account.username)
            mailbox.pass_(password)
            total_messages = len(mailbox.list()[1])
            start_index = max(1, total_messages - 199)

            for index in range(total_messages, start_index - 1, -1):
                _, lines, _ = mailbox.retr(index)
                raw_message = b"\n".join(lines)
                msg = email.message_from_bytes(raw_message, policy=policy.default)
                message_id = self._message_id_for(msg, index)
                if message_id in known_ids:
                    continue

                attachments: list[RawAttachment] = []
                body_text_parts: list[str] = []
                body_html_parts: list[str] = []

                for part in msg.walk():
                    content_disposition = part.get_content_disposition()
                    content_type = part.get_content_type()

                    if part.is_multipart():
                        continue

                    if content_disposition == "attachment":
                        filename = part.get_filename()
                        ext = Path(filename or "").suffix.lower()
                        if ext not in INVOICE_EXTENSIONS:
                            continue
                        attachments.append(
                            RawAttachment(
                                filename=_resolve_filename(filename, f"attachment{ext or '.bin'}"),
                                payload=part.get_payload(decode=True) or b"",
                                content_type=content_type or "application/octet-stream",
                            )
                        )
                        continue

                    payload = part.get_content()
                    if not isinstance(payload, str) or not payload:
                        continue
                    if content_type == "text/plain":
                        body_text_parts.append(payload)
                    elif content_type == "text/html":
                        body_html_parts.append(payload)

                body_text = "\n".join(body_text_parts).strip()
                body_html = "\n".join(body_html_parts).strip()
                if not body_text and body_html:
                    body_text = _html_to_text(body_html)

                emails.append(
                    RawEmail(
                        uid=message_id,
                        subject=str(msg.get("Subject") or ""),
                        body_text=body_text,
                        body_html=body_html,
                        from_addr=str(msg.get("From") or ""),
                        received_at=_normalize_datetime(msg.get("Date")),
                        attachments=attachments,
                        body_links=_extract_urls(body_text, body_html),
                    )
                )
        finally:
            try:
                mailbox.quit()
            except POP3_CONNECTION_ERRORS:
                mailbox.close()

        return emails

    async def test_connection(self, account: EmailAccount) -> bool:
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, functools.partial(self._test_sync, account))
        except POP3_CONNECTION_ERRORS as exc:
            logger.exception("POP3 connection test failed for account %s: %s", account.id, exc)
            return False

    def _test_sync(self, account: EmailAccount) -> bool:
        password = decrypt_password(account.password_encrypted)
        mailbox = poplib.POP3_SSL(account.host or "", account.port or 995)
        try:
            mailbox.user(account.username)
            mailbox.pass_(password)
            return True
        finally:
            try:
                mailbox.quit()
            except POP3_CONNECTION_ERRORS:
                mailbox.close()

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
            except (AttributeError, KeyError, TypeError):
                header_value = None

            if isinstance(header_value, list):
                message_id = str(header_value[0]) if header_value else ""
            elif header_value is not None:
                message_id = str(header_value)

        if not message_id and hasattr(msg, "get"):
            header_value = msg.get("Message-ID") or msg.get("Message-Id")
            if header_value is not None:
                message_id = str(header_value)

        if message_id:
            return message_id.strip()

        fallback_bits = [
            cast(str, getattr(msg, "subject", None) or (msg.get("Subject") if hasattr(msg, "get") else "") or ""),
            cast(str, getattr(msg, "from_", None) or (msg.get("From") if hasattr(msg, "get") else "") or ""),
            str(getattr(msg, "date", None) or (msg.get("Date") if hasattr(msg, "get") else "") or ""),
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
        except OUTLOOK_CONNECTION_ERRORS as exc:
            logger.exception("Outlook connection test failed for account %s: %s", account.id, exc)
            return False

    async def has_cached_token_async(self, account: EmailAccount) -> bool:
        try:
            await self._acquire_access_token(account)
        except RuntimeError:
            return False
        return True

    async def initiate_device_flow_async(self, account: EmailAccount) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(self._initiate_device_flow_sync, account))

    async def complete_device_flow_async(self, account: EmailAccount, flow: dict[str, Any]) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(self._complete_device_flow_sync, account, flow))

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
            raise RuntimeError("Outlook authorization required. Use the Settings page to authenticate.")

        self._save_cache(account, token_cache)
        return result or {}

    def _initiate_device_flow_sync(self, account: EmailAccount) -> dict[str, Any]:
        token_cache = self._load_cache(account)
        app = msal.PublicClientApplication(
            client_id=account.username,
            authority="https://login.microsoftonline.com/common",
            token_cache=token_cache,
        )
        flow = cast(dict[str, Any], app.initiate_device_flow(scopes=self.SCOPES))
        if "user_code" not in flow:
            raise RuntimeError("Failed to start Outlook device flow")
        return flow

    def _complete_device_flow_sync(self, account: EmailAccount, flow: dict[str, Any]) -> dict[str, Any]:
        token_cache = self._load_cache(account)
        app = msal.PublicClientApplication(
            client_id=account.username,
            authority="https://login.microsoftonline.com/common",
            token_cache=token_cache,
        )
        result = cast(dict[str, Any], app.acquire_token_by_device_flow(flow))
        self._save_cache(account, token_cache)
        return result

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
    "OAuthFlowRegistry",
    "OAuthFlowState",
    "OutlookScanner",
    "Pop3Scanner",
    "RawAttachment",
    "RawEmail",
    "ScannerFactory",
    "decrypt_password",
    "encrypt_password",
    "oauth_registry",
]
