from __future__ import annotations

import base64
import json
import poplib
import ssl
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from imap_tools.errors import MailboxLoginError
import httpx
from msal.exceptions import MsalServiceError
import pytest

import app.services.email_scanner as email_scanner
from app.models import EmailAccount
from app.services.email_scanner import (
    FIRST_SCAN_LIMIT,
    ImapScanner,
    OAuthFlowRegistry,
    OAuthFlowState,
    OutlookScanner,
    Pop3Scanner,
    ScannerFactory,
    _get_outlook_msal_params,
    _derive_fernet_key,
    _extract_urls,
    _html_to_text,
    _is_personal_microsoft_account,
    _is_uid_newer,
    _normalize_datetime,
    _resolve_filename,
    decrypt_password,
    encrypt_password,
)


def test_helpers_cover_password_url_html_datetime_and_uid_logic(settings) -> None:
    encrypted = encrypt_password("secret", settings.JWT_SECRET)
    assert decrypt_password(encrypted, settings.JWT_SECRET) == "secret"
    assert decrypt_password(None, settings.JWT_SECRET) == ""
    assert _derive_fernet_key("abc") == _derive_fernet_key("abc")
    assert _extract_urls("visit https://a.test", '<a href="https://a.test">x</a> https://b.test') == [
        "https://a.test",
        "https://b.test",
    ]
    assert _html_to_text("") == ""
    assert _html_to_text("<p>Hello&nbsp;World</p>") == "Hello World"
    aware = datetime(2024, 1, 1, tzinfo=timezone.utc)
    naive = datetime(2024, 1, 1)
    assert _normalize_datetime(aware) is aware
    assert _normalize_datetime(naive).tzinfo is not None
    assert _normalize_datetime("2024-01-01T00:00:00Z").tzinfo is not None
    assert _normalize_datetime(None).tzinfo is not None
    assert _is_uid_newer(None, "1") is False
    assert _is_uid_newer("2", None) is True
    assert _is_uid_newer("10", "2") is True
    assert _is_uid_newer("b", "a") is True
    assert _resolve_filename("  ", "fallback.pdf") == "fallback.pdf"


def test_is_personal_microsoft_account_detects_domains() -> None:
    assert _is_personal_microsoft_account("person@outlook.com") is True
    assert _is_personal_microsoft_account("person@LIVE.CN") is True
    assert _is_personal_microsoft_account("person@company.com") is False
    assert _is_personal_microsoft_account("not-an-email") is False


def test_get_outlook_msal_params_resolves_personal_and_organizational(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(email_scanner, "get_settings", lambda: settings)

    personal = EmailAccount(id=1, name="o", type="outlook", username="person@outlook.com", outlook_account_type="personal")
    organizational = EmailAccount(
        id=2,
        name="o",
        type="outlook",
        username="person@company.com",
        outlook_account_type="organizational",
    )

    assert _get_outlook_msal_params(personal) == (
        settings.OUTLOOK_PERSONAL_CLIENT_ID,
        "https://login.microsoftonline.com/consumers",
    )
    assert _get_outlook_msal_params(organizational) == (
        settings.OUTLOOK_AAD_CLIENT_ID,
        "https://login.microsoftonline.com/common",
    )


class _FakeContextManager:
    def __init__(self, mailbox):
        self.mailbox = mailbox

    def __enter__(self):
        return self.mailbox

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeFolderInfo:
    def __init__(self, name: str, flags: tuple = ()):
        self.name = name
        self.flags = flags


class _FakeFolderManager:
    def __init__(self, folders=None, uidvalidity_map=None, uidnext_map=None, messages_map=None):
        self._folders = folders if folders is not None else [_FakeFolderInfo("INBOX")]
        self._uidvalidity_map = uidvalidity_map or {}
        self._uidnext_map = uidnext_map or {}
        self._messages_map = messages_map or {}
        self.current: str = "INBOX"

    def list(self):
        return self._folders

    def set(self, name: str) -> None:
        self.current = name

    def status(self, name: str, items) -> dict:
        result: dict = {}
        requested = set(items or [])
        if not requested or "UIDVALIDITY" in requested:
            result["UIDVALIDITY"] = self._uidvalidity_map.get(name, 12345)
        if "UIDNEXT" in requested:
            result["UIDNEXT"] = self._uidnext_map.get(name, 0)
        if "MESSAGES" in requested:
            result["MESSAGES"] = self._messages_map.get(name, 0)
        return result


def test_imap_scan_sync_filters_attachments_and_last_uid(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    msg_old = SimpleNamespace(uid="1", subject="old", text="see https://skip.test", html="", from_="a@test", date=datetime.now(), attachments=[], headers={})
    msg_new = SimpleNamespace(
        uid="3",
        subject="new",
        text="body https://invoice.test",
        html="<p>html</p>",
        from_="b@test",
        date="2024-01-01T00:00:00Z",
        attachments=[
            SimpleNamespace(filename="invoice.pdf", payload=b"pdf", content_type="application/pdf"),
            SimpleNamespace(filename="note.txt", payload=b"txt", content_type="text/plain"),
            SimpleNamespace(filename=None, payload=b"xml", content_type=None),
        ],
        headers={},
    )

    class FakeMailbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                self.host = host
                self.port = port
                self.folder = _FakeFolderManager()

        def login(self, username, password, **kwargs):
                del kwargs
                assert username == "user@example.com"
                assert password == "secret"
                return _FakeContextManager(self)

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria
            assert limit is None
            assert reverse is True
            assert mark_seen is False
            assert headers_only is True
            assert bulk == 500
            return [msg_old, msg_new]

    monkeypatch.setattr(email_scanner, "AND", lambda **kwargs: kwargs or {"all": True})
    monkeypatch.setattr(email_scanner, "MailBox", FakeMailbox)
    account = EmailAccount(
        id=1,
        name="imap",
        type="imap",
        host="imap.example.com",
        port=993,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    scanner = ImapScanner()

    emails = scanner._scan_sync(account, "2")

    assert len(emails) == 1
    assert emails[0].uid == "3"
    assert emails[0].is_hydrated is False
    assert emails[0].body_text == ""
    assert emails[0].body_html == ""
    assert emails[0].body_links == []
    assert emails[0].attachments == []


def test_imap_scan_sync_uses_first_scan_limit(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    captured: dict[str, object] = {}

    class FakeMailbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self.folder = _FakeFolderManager()

        def login(self, username, password, **kwargs):
                del kwargs
                assert username == "user@example.com"
                assert password == "secret"
                return _FakeContextManager(self)

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            captured.update({"criteria": criteria, "limit": limit, "reverse": reverse, "mark_seen": mark_seen, "headers_only": headers_only, "bulk": bulk})
            return []

    monkeypatch.setattr(email_scanner, "AND", lambda **kwargs: kwargs or {"all": True})
    monkeypatch.setattr(email_scanner, "MailBox", FakeMailbox)
    account = EmailAccount(
        id=1,
        name="imap",
        type="imap",
        host="imap.example.com",
        port=993,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )

    ImapScanner()._scan_sync(account, None)

    assert captured == {
        "criteria": "ALL",
        "limit": FIRST_SCAN_LIMIT,
        "reverse": True,
        "mark_seen": False,
        "headers_only": True,
        "bulk": 500,
    }


@pytest.mark.asyncio
async def test_imap_hydrate_email_returns_early_if_already_hydrated(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    account = EmailAccount(
        id=1,
        name="imap",
        type="imap",
        host="imap.example.com",
        port=993,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    email = email_scanner.RawEmail(
        uid="1",
        subject="s",
        body_text="already body",
        body_html="",
        from_addr="a@test",
        received_at=datetime.now(timezone.utc),
        is_hydrated=True,
    )
    result = await ImapScanner().hydrate_email(account, email)
    assert result is email
    assert result.body_text == "already body"


@pytest.mark.asyncio
async def test_imap_hydrate_email_fetches_body_and_attachments(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    msg = SimpleNamespace(
        uid="3",
        subject="s",
        text="body https://x.test",
        html="<p>html</p>",
        from_="a@test",
        date="2024-01-01T00:00:00Z",
        attachments=[
            SimpleNamespace(filename="invoice.pdf", payload=b"PDFBYTES", content_type="application/pdf"),
            SimpleNamespace(filename="note.txt", payload=b"noop", content_type="text/plain"),
        ],
    )

    class FakeMailbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self.folder = _FakeFolderManager()

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                return _FakeContextManager(self)

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, reverse, headers_only
            assert mark_seen is False
            assert limit == 1
            assert bulk is False
            return [msg]

    monkeypatch.setattr(email_scanner, "AND", lambda **kwargs: kwargs)
    monkeypatch.setattr(email_scanner, "MailBox", FakeMailbox)
    account = EmailAccount(
        id=1,
        name="imap",
        type="imap",
        host="imap.example.com",
        port=993,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    email = email_scanner.RawEmail(
        uid="3",
        subject="s",
        body_text="",
        body_html="",
        from_addr="",
        received_at=datetime.now(timezone.utc),
        is_hydrated=False,
    )
    result = await ImapScanner().hydrate_email(account, email)
    assert result.is_hydrated is True
    assert result.body_text == "body https://x.test"
    assert result.body_links == ["https://x.test"]
    assert len(result.attachments) == 1
    assert result.attachments[0].filename == "invoice.pdf"
    assert result.attachments[0].payload == b"PDFBYTES"
    assert result.attachments[0].size == len(b"PDFBYTES")


@pytest.mark.asyncio
async def test_imap_hydrate_email_handles_connection_failure(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    class BrokenMailbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                raise OSError("imap broken")

    monkeypatch.setattr(email_scanner, "MailBox", BrokenMailbox)
    warnings: list[str] = []
    monkeypatch.setattr(email_scanner.logger, "warning", lambda msg, *args: warnings.append(msg % args))

    account = EmailAccount(
        id=1,
        name="imap",
        type="imap",
        host="imap.example.com",
        port=993,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    email = email_scanner.RawEmail(
        uid="3",
        subject="s",
        body_text="",
        body_html="",
        from_addr="",
        received_at=datetime.now(timezone.utc),
        is_hydrated=False,
    )
    result = await ImapScanner().hydrate_email(account, email)
    assert result.is_hydrated is True
    assert result.attachments == []
    assert any("IMAP hydrate failed" in w for w in warnings)


@pytest.mark.asyncio
async def test_imap_hydrate_email_handles_empty_fetch(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    class EmptyMailbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self.folder = _FakeFolderManager()

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                return _FakeContextManager(self)

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            return []

    monkeypatch.setattr(email_scanner, "AND", lambda **kwargs: kwargs)
    monkeypatch.setattr(email_scanner, "MailBox", EmptyMailbox)

    account = EmailAccount(
        id=1,
        name="imap",
        type="imap",
        host="imap.example.com",
        port=993,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    email = email_scanner.RawEmail(
        uid="3",
        subject="s",
        body_text="",
        body_html="",
        from_addr="",
        received_at=datetime.now(timezone.utc),
        is_hydrated=False,
    )
    result = await ImapScanner().hydrate_email(account, email)
    assert result.is_hydrated is True
    assert result.attachments == []


@pytest.mark.asyncio
async def test_imap_test_connection_success_and_failure(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    account = EmailAccount(
        id=1,
        name="imap",
        type="imap",
        host="imap.example.com",
        port=993,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    scanner = ImapScanner()

    class Loop:
        async def run_in_executor(self, executor, func):
            del executor
            return func()

    class ConnectionMailbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                return _FakeContextManager(self)

    monkeypatch.setattr(email_scanner.asyncio, "get_running_loop", lambda: Loop())
    monkeypatch.setattr(email_scanner, "MailBox", ConnectionMailbox)
    assert await scanner.test_connection(account) is True

    failures: list[str] = []

    class BrokenLoop:
        def __init__(self, error: Exception):
            self.error = error

        async def run_in_executor(self, executor, func):
            del executor, func
            raise self.error

    monkeypatch.setattr(email_scanner.logger, "exception", lambda message, *args: failures.append(message % args))
    monkeypatch.setattr(email_scanner.asyncio, "get_running_loop", lambda: BrokenLoop(OSError("imap down")))
    assert await scanner.test_connection(account) is False
    assert any("IMAP connection test failed for account 1: imap down" in failure for failure in failures)

    monkeypatch.setattr(
        email_scanner.asyncio,
        "get_running_loop",
        lambda: BrokenLoop(MailboxLoginError(("NO", [b"bad credentials"]), "OK")),
    )
    assert await scanner.test_connection(account) is False
    assert any("bad credentials" in failure for failure in failures)

    monkeypatch.setattr(email_scanner.asyncio, "get_running_loop", lambda: BrokenLoop(RuntimeError("fail")))
    with pytest.raises(RuntimeError, match="fail"):
        await scanner.test_connection(account)


@pytest.mark.asyncio
async def test_async_scan_wrappers_for_imap_and_pop3(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    account = EmailAccount(id=1, name="a", type="imap", username="u", password_encrypted=encrypt_password("secret", settings.JWT_SECRET))

    class Loop:
        async def run_in_executor(self, executor, func):
            del executor
            return func()

    monkeypatch.setattr(email_scanner.asyncio, "get_running_loop", lambda: Loop())
    monkeypatch.setattr(ImapScanner, "_scan_sync", lambda self, account, last_uid, options=None, progress_callback=None: ["imap"])
    monkeypatch.setattr(Pop3Scanner, "_scan_sync", lambda self, account, last_uid, options=None: ["pop3"])
    assert await ImapScanner().scan(account, "1") == ["imap"]
    assert await Pop3Scanner().scan(account, "1") == ["pop3"]


def test_pop3_helpers_and_scan_sync(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    scanner = Pop3Scanner()
    assert scanner._load_recent_ids(None) == set()
    assert scanner._load_recent_ids("bad-json") == {"bad-json"}
    assert scanner._load_recent_ids(json.dumps(["1", "2"])) == {"1", "2"}
    assert scanner._load_recent_ids(json.dumps({"a": 1})) == set()
    assert scanner.serialize_recent_ids([str(i) for i in range(1500)]).startswith("[")
    msg_with_header = SimpleNamespace(headers={"Message-ID": ["  abc  "]})
    assert scanner._message_id_for(msg_with_header, 1) == "abc"
    fallback_id = scanner._message_id_for(SimpleNamespace(subject="s", from_="f", date="d", headers=None), 1)
    assert fallback_id.startswith("pop3-")

    message = b"\r\n".join(
        [
            b"From: sender",
            b"Subject: invoice",
            b"Date: Mon, 01 Jan 2024 00:00:00 +0000",
            b"Message-ID: mid-1",
            b"MIME-Version: 1.0",
            b"Content-Type: multipart/mixed; boundary=sep",
            b"",
            b"--sep",
            b"Content-Type: text/plain; charset=utf-8",
            b"",
            b"text https://invoice.test",
            b"--sep",
            b"Content-Type: application/xml",
            b"Content-Disposition: attachment; filename=invoice.xml",
            b"Content-Transfer-Encoding: base64",
            b"",
            b"PHgvPg==",
            b"--sep--",
        ]
    )

    class FakePop3:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port

        def user(self, username):
            assert username == "user@example.com"

        def pass_(self, password):
            assert password == "secret"

        def list(self):
            return b"+OK", [b"1 123"], 123

        def retr(self, index):
            assert index == 1
            return b"+OK", message.split(b"\r\n"), len(message)

        def quit(self):
            return b"+OK"

        def close(self):
            return None

    monkeypatch.setattr(email_scanner.poplib, "POP3_SSL", FakePop3)
    account = EmailAccount(
        id=2,
        name="pop3",
        type="pop3",
        host="pop.example.com",
        port=995,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )

    emails = scanner._scan_sync(account, json.dumps(["already-seen"]))
    assert emails[0].uid == "mid-1"
    assert emails[0].attachments[0].filename == "invoice.xml"

    retr_indexes: list[int] = []

    class PaginationPop3(FakePop3):
        def list(self):
            return b"+OK", [f"{idx} 123".encode() for idx in range(1, 6)], 123

        def retr(self, index):
            retr_indexes.append(index)
            payload = b"\r\n".join(
                [
                    b"From: sender",
                    f"Subject: {index}".encode(),
                    b"Date: Mon, 01 Jan 2024 00:00:00 +0000",
                    f"Message-ID: mid-{index}".encode(),
                    b"",
                ]
            )
            return b"+OK", payload.split(b"\r\n"), len(payload)

    monkeypatch.setattr(email_scanner.poplib, "POP3_SSL", PaginationPop3)
    scanner._scan_sync(account, "mid-3")
    assert retr_indexes == [5, 4, 3]

    class SkipPop3(FakePop3):
        def retr(self, index):
            payload = b"\r\n".join(
                [
                    b"From: sender",
                    b"Subject: skip",
                    b"Date: Mon, 01 Jan 2024 00:00:00 +0000",
                    b"Message-ID: mid-1",
                    b"",
                ]
            )
            return b"+OK", payload.split(b"\r\n"), len(payload)

    monkeypatch.setattr(email_scanner.poplib, "POP3_SSL", SkipPop3)
    assert scanner._scan_sync(account, json.dumps(["mid-1"])) == []

    retr_indexes_first: list[int] = []

    class FirstScanPop3(PaginationPop3):
        def retr(self, index):
            retr_indexes_first.append(index)
            return super().retr(index)

    monkeypatch.setattr(email_scanner.poplib, "POP3_SSL", FirstScanPop3)
    scanner._scan_sync(account, None)
    assert retr_indexes_first == [5, 4, 3, 2, 1]

    retr_indexes_capped: list[int] = []

    class CappedFirstScanPop3(PaginationPop3):
        def retr(self, index):
            retr_indexes_capped.append(index)
            return super().retr(index)

    monkeypatch.setattr(email_scanner, "FIRST_SCAN_LIMIT", 2)
    monkeypatch.setattr(email_scanner.poplib, "POP3_SSL", CappedFirstScanPop3)
    scanner._scan_sync(account, None)
    assert retr_indexes_capped == [5, 4]

    class NotePop3(FakePop3):
        def retr(self, index):
            payload = b"\r\n".join(
                [
                    b"From: sender",
                    b"Subject: note",
                    b"Date: Mon, 01 Jan 2024 00:00:00 +0000",
                    b"Message-ID: mid-2",
                    b"MIME-Version: 1.0",
                    b"Content-Type: multipart/mixed; boundary=sep",
                    b"",
                    b"--sep",
                    b"Content-Type: text/plain; charset=utf-8",
                    b"",
                    b"",
                    b"--sep",
                    b"Content-Type: text/plain",
                    b"Content-Disposition: attachment; filename=note.txt",
                    b"",
                    b"x",
                    b"--sep--",
                ]
            )
            return b"+OK", payload.split(b"\r\n"), len(payload)

    monkeypatch.setattr(email_scanner.poplib, "POP3_SSL", NotePop3)
    note_emails = scanner._scan_sync(account, None)
    assert note_emails[0].attachments == []

    class HtmlOnlyPop3(FakePop3):
        def retr(self, index):
            payload = b"\r\n".join(
                [
                    b"From: sender",
                    b"Subject: html",
                    b"Date: Mon, 01 Jan 2024 00:00:00 +0000",
                    b"Message-ID: mid-3",
                    b"MIME-Version: 1.0",
                    b"Content-Type: multipart/alternative; boundary=sep",
                    b"",
                    b"--sep",
                    b"Content-Type: text/html; charset=utf-8",
                    b"",
                    b"<p>Hello invoice</p>",
                    b"--sep--",
                ]
            )
            return b"+OK", payload.split(b"\r\n"), len(payload)

    monkeypatch.setattr(email_scanner.poplib, "POP3_SSL", HtmlOnlyPop3)
    html_emails = scanner._scan_sync(account, None)
    assert html_emails[0].body_html == "<p>Hello invoice</p>"
    assert html_emails[0].body_text == "Hello invoice"

    class OtherTextPartPop3(FakePop3):
        def retr(self, index):
            payload = b"\r\n".join(
                [
                    b"From: sender",
                    b"Subject: calendar",
                    b"Date: Mon, 01 Jan 2024 00:00:00 +0000",
                    b"Message-ID: mid-4",
                    b"MIME-Version: 1.0",
                    b"Content-Type: multipart/alternative; boundary=sep",
                    b"",
                    b"--sep",
                    b"Content-Type: text/calendar; charset=utf-8",
                    b"",
                    b"BEGIN:VCALENDAR",
                    b"--sep--",
                ]
            )
            return b"+OK", payload.split(b"\r\n"), len(payload)

    monkeypatch.setattr(email_scanner.poplib, "POP3_SSL", OtherTextPartPop3)
    other_text_emails = scanner._scan_sync(account, None)
    assert other_text_emails[0].body_text == ""
    assert other_text_emails[0].body_html == ""

    class CloseFallbackPop3(FakePop3):
        def __init__(self, host, port, **kwargs):
                del kwargs
                super().__init__(host, port)
                self.closed = False

        def quit(self):
            raise poplib.error_proto("-ERR quit failed")

        def close(self):
            self.closed = True
            return None

    close_mailbox: CloseFallbackPop3 | None = None

    def build_close_fallback(host, port):
        nonlocal close_mailbox
        close_mailbox = CloseFallbackPop3(host, port)
        return close_mailbox

    monkeypatch.setattr(email_scanner.poplib, "POP3_SSL", build_close_fallback)
    scanner._scan_sync(account, None)
    assert close_mailbox is not None and close_mailbox.closed is True


@pytest.mark.asyncio
async def test_pop3_test_connection_failure(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    scanner = Pop3Scanner()
    account = EmailAccount(
        id=2,
        name="pop3",
        type="pop3",
        host="pop.example.com",
        port=995,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )

    failures: list[str] = []

    class BrokenLoop:
        def __init__(self, error: Exception):
            self.error = error

        async def run_in_executor(self, executor, func):
            del executor, func
            raise self.error

    monkeypatch.setattr(email_scanner.logger, "exception", lambda message, *args: failures.append(message % args))
    monkeypatch.setattr(email_scanner.asyncio, "get_running_loop", lambda: BrokenLoop(OSError("pop down")))
    assert await scanner.test_connection(account) is False
    assert any("POP3 connection test failed for account 2: pop down" in failure for failure in failures)

    monkeypatch.setattr(email_scanner.asyncio, "get_running_loop", lambda: BrokenLoop(poplib.error_proto("-ERR auth")))
    assert await scanner.test_connection(account) is False
    assert any("-ERR auth" in failure for failure in failures)

    monkeypatch.setattr(email_scanner.asyncio, "get_running_loop", lambda: BrokenLoop(RuntimeError("fail")))
    with pytest.raises(RuntimeError, match="fail"):
        await scanner.test_connection(account)


def test_pop3_test_sync_and_message_id_edge_cases(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    scanner = Pop3Scanner()
    account = EmailAccount(id=2, name="pop3", type="pop3", host="pop.example.com", port=995, username="user@example.com", password_encrypted=encrypt_password("secret", settings.JWT_SECRET))

    class FakePop3:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port

        def user(self, username):
            assert username == "user@example.com"

        def pass_(self, password):
            assert password == "secret"

        def quit(self):
            return b"+OK"

        def close(self):
            return None

    monkeypatch.setattr(email_scanner.poplib, "POP3_SSL", FakePop3)
    assert scanner._test_sync(account) is True

    class QuitFailsPop3(FakePop3):
        def __init__(self, host, port, **kwargs):
                del kwargs
                super().__init__(host, port)
                self.closed = False

        def quit(self):
            raise poplib.error_proto("-ERR quit failed")

        def close(self):
            self.closed = True
            return None

    quit_fail_mailbox: QuitFailsPop3 | None = None

    def build_quit_fail(host, port):
        nonlocal quit_fail_mailbox
        quit_fail_mailbox = QuitFailsPop3(host, port)
        return quit_fail_mailbox

    monkeypatch.setattr(email_scanner.poplib, "POP3_SSL", build_quit_fail)
    assert scanner._test_sync(account) is True
    assert quit_fail_mailbox is not None and quit_fail_mailbox.closed is True


def test_pop3_message_id_for_string_and_email_message_sources() -> None:
    scanner = Pop3Scanner()

    assert scanner._message_id_for(SimpleNamespace(headers={"Message-ID": " scalar "}, subject="s", from_="f", date="d"), 1) == "scalar"
    assert scanner._message_id_for(email_scanner.email.message_from_bytes(b"Message-ID: msg-get\r\n\r\n", policy=email_scanner.policy.default), 3) == "msg-get"
    assert scanner._message_id_for(email_scanner.email.message_from_bytes(b"Subject: no-id\r\n\r\n", policy=email_scanner.policy.default), 4).startswith("pop3-")


def test_pop3_message_id_for_handles_key_error_headers() -> None:
    scanner = Pop3Scanner()

    class BrokenHeaders:
        def __getitem__(self, key):
            raise KeyError(key)

    assert scanner._message_id_for(SimpleNamespace(headers=BrokenHeaders(), subject="s", from_="f", date="d"), 2).startswith("pop3-")


@pytest.mark.asyncio
async def test_outlook_scanner_scan_connection_and_helpers(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    token_path = tmp_path / "tokens" / "cache.json"
    account = EmailAccount(
        id=3,
        name="outlook",
        type="outlook",
        username="client-id",
        oauth_token_path=str(token_path),
    )
    scanner = OutlookScanner()

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, *args, **kwargs):
            del args, kwargs
            self.calls = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

        async def get(self, url, headers=None, params=None):
            del headers
            self.calls.append((url, params))
            if "/mailFolders" in url and "/messages" not in url and "/childFolders" not in url and "/attachments" not in url:
                return FakeResponse(
                    {
                        "value": [
                            {
                                "id": "folder-inbox",
                                "displayName": "Inbox",
                                "wellKnownName": "inbox",
                                "totalItemCount": 1,
                                "childFolderCount": 0,
                            }
                        ],
                        "@odata.nextLink": None,
                    }
                )
            if url.endswith("/messages") or "/messages?" in url:
                return FakeResponse(
                    {
                        "value": [
                            {
                                "id": "graph-1",
                                "internetMessageId": "uid-1",
                                "subject": "Invoice",
                                "bodyPreview": "hello https://invoice.test",
                                "from": {"emailAddress": {"address": "sender@test"}},
                                "receivedDateTime": "2024-01-01T00:00:00Z",
                                "hasAttachments": True,
                            }
                        ],
                        "@odata.nextLink": None,
                    }
                )
            if "/attachments" not in url and "/messages/" in url:
                return FakeResponse(
                    {"body": {"contentType": "html", "content": "<p>hello https://invoice.test</p>"}}
                )
            return FakeResponse(
                {
                    "value": [
                        {
                            "@odata.type": "#microsoft.graph.fileAttachment",
                            "name": "invoice.pdf",
                            "contentBytes": base64.b64encode(b"pdf").decode(),
                            "contentType": "application/pdf",
                        },
                        {"@odata.type": "#microsoft.graph.itemAttachment", "name": "skip.pdf"},
                        {
                            "@odata.type": "#microsoft.graph.fileAttachment",
                            "name": "skip.txt",
                            "contentBytes": base64.b64encode(b"txt").decode(),
                            "contentType": "text/plain",
                        },
                    ]
                }
            )

    monkeypatch.setattr(email_scanner.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(scanner, "_acquire_access_token", AsyncMock(return_value="token"))

    emails = await scanner.scan(account)
    assert emails[0].uid == "uid-1"
    assert emails[0].is_hydrated is False
    assert emails[0].attachments == []
    assert emails[0].body_links == []
    hydrated = await scanner.hydrate_email(account, emails[0])
    assert hydrated.attachments[0].filename == "invoice.pdf"
    assert hydrated.body_links == ["https://invoice.test"]
    assert hydrated.is_hydrated is True
    assert await scanner.test_connection(account) is True

    failures: list[str] = []
    monkeypatch.setattr(email_scanner.logger, "exception", lambda message, *args: failures.append(message % args))
    monkeypatch.setattr(scanner, "_acquire_access_token", AsyncMock(side_effect=httpx.HTTPError("boom")))
    assert await scanner.test_connection(account) is False
    assert any("Outlook connection test failed for account 3: boom" in failure for failure in failures)

    monkeypatch.setattr(
        scanner,
        "_acquire_access_token",
        AsyncMock(side_effect=MsalServiceError(error="service_error", error_description="msal boom")),
    )
    assert await scanner.test_connection(account) is False

    monkeypatch.setattr(scanner, "_acquire_access_token", AsyncMock(side_effect=RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        await scanner.test_connection(account)

    assert scanner._recent_message_filter().startswith("receivedDateTime ge ")


def test_pop3_close_fallback_ignores_expected_transport_errors(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    scanner = Pop3Scanner()
    account = EmailAccount(
        id=2,
        name="pop3",
        type="pop3",
        host="pop.example.com",
        port=995,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )

    class CloseFallbackPop3:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self.closed = False

        def user(self, username):
            assert username == "user@example.com"

        def pass_(self, password):
            assert password == "secret"

        def quit(self):
            raise ssl.SSLError("bye")

        def close(self):
            self.closed = True

    mailbox: CloseFallbackPop3 | None = None

    def build_mailbox(host, port):
        nonlocal mailbox
        mailbox = CloseFallbackPop3(host, port)
        return mailbox

    monkeypatch.setattr(email_scanner.poplib, "POP3_SSL", build_mailbox)
    assert scanner._test_sync(account) is True
    assert mailbox is not None and mailbox.closed is True


def test_pop3_close_fallback_propagates_unexpected_quit_error(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    scanner = Pop3Scanner()
    account = EmailAccount(
        id=2,
        name="pop3",
        type="pop3",
        host="pop.example.com",
        port=995,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )

    class UnexpectedQuitPop3:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port

        def user(self, username):
            assert username == "user@example.com"

        def pass_(self, password):
            assert password == "secret"

        def quit(self):
            raise RuntimeError("quit failed")

        def close(self):
            raise AssertionError("close should not be called")

    monkeypatch.setattr(email_scanner.poplib, "POP3_SSL", UnexpectedQuitPop3)
    with pytest.raises(RuntimeError, match="quit failed"):
        scanner._test_sync(account)


def test_pop3_message_id_for_handles_specific_header_errors() -> None:
    scanner = Pop3Scanner()

    class TypeErrorHeaders:
        def __getitem__(self, key):
            raise TypeError(key)

    assert scanner._message_id_for(SimpleNamespace(headers=TypeErrorHeaders(), subject="s", from_="f", date="d"), 1).startswith("pop3-")


@pytest.mark.asyncio
async def test_outlook_scan_multi_folder_and_pagination(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    scanner = OutlookScanner()
    account = EmailAccount(id=4, name="outlook", type="outlook", username="client-id", oauth_token_path=str(tmp_path / "cache.json"))

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    folder_inbox = {"id": "f-inbox", "displayName": "Inbox", "wellKnownName": "inbox", "totalItemCount": 2, "childFolderCount": 0}
    folder_archive = {"id": "f-archive", "displayName": "Archive", "wellKnownName": "archive", "totalItemCount": 1, "childFolderCount": 0}
    folder_drafts = {"id": "f-drafts", "displayName": "Drafts", "wellKnownName": "drafts", "totalItemCount": 5, "childFolderCount": 0}

    inbox_msgs = [
        {"id": "g1", "internetMessageId": "uid-1", "subject": "Inv A", "bodyPreview": "", "from": {"emailAddress": {"address": "a@test"}}, "receivedDateTime": "2024-01-01T00:00:00Z", "hasAttachments": False},
        {"id": "g2", "internetMessageId": "uid-2", "subject": "Inv B", "bodyPreview": "", "from": {"emailAddress": {"address": "b@test"}}, "receivedDateTime": "2024-02-01T00:00:00Z", "hasAttachments": False},
    ]
    archive_msgs = [
        {"id": "g3", "internetMessageId": "uid-3", "subject": "Inv C", "bodyPreview": "", "from": {"emailAddress": {"address": "c@test"}}, "receivedDateTime": "2024-03-01T00:00:00Z", "hasAttachments": False},
    ]
    urls: list[str] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

        async def get(self, url, headers=None, params=None):
            del headers, params
            urls.append(url)
            if "/mailFolders" in url and "f-inbox" not in url and "f-archive" not in url and "/messages" not in url and "/childFolders" not in url:
                return FakeResponse({"value": [folder_inbox, folder_archive, folder_drafts], "@odata.nextLink": None})
            if "f-inbox" in url and "/messages" in url:
                return FakeResponse({"value": inbox_msgs, "@odata.nextLink": None})
            if "f-archive" in url and "/messages" in url:
                return FakeResponse({"value": archive_msgs, "@odata.nextLink": None})
            return FakeResponse({"value": [], "@odata.nextLink": None})

    monkeypatch.setattr(email_scanner.httpx, "AsyncClient", lambda timeout: FakeClient())
    monkeypatch.setattr(scanner, "_acquire_access_token", AsyncMock(return_value="token"))

    emails = await scanner.scan(account, last_uid=None)
    assert len(emails) == 3
    assert {e.uid for e in emails} == {"uid-1", "uid-2", "uid-3"}
    assert all(e.folder in {"Inbox", "Archive"} for e in emails)
    drafts_urls = [u for u in urls if "f-drafts" in u]
    assert drafts_urls == [], "drafts folder must not be scanned"
    state = json.loads(scanner._last_scan_state)
    assert "f-inbox" in state
    assert "f-archive" in state
    assert "f-drafts" not in state


@pytest.mark.asyncio
async def test_outlook_scan_incremental_uses_per_folder_watermark(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    scanner = OutlookScanner()
    account = EmailAccount(id=5, name="outlook", type="outlook", username="client-id", oauth_token_path=str(tmp_path / "cache.json"))

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    folder = {"id": "f-inbox", "displayName": "Inbox", "wellKnownName": "inbox", "totalItemCount": 2, "childFolderCount": 0}
    seen_params: list = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

        async def get(self, url, headers=None, params=None):
            del headers
            if "/mailFolders" in url and "/messages" not in url:
                return FakeResponse({"value": [folder], "@odata.nextLink": None})
            seen_params.append(params)
            return FakeResponse({"value": [], "@odata.nextLink": None})

    monkeypatch.setattr(email_scanner.httpx, "AsyncClient", lambda timeout: FakeClient())
    monkeypatch.setattr(scanner, "_acquire_access_token", AsyncMock(return_value="token"))

    prior_state = json.dumps({"f-inbox": "2024-06-01T00:00:00Z"})
    await scanner.scan(account, last_uid=prior_state)

    assert seen_params[0] is not None
    assert "$filter" in seen_params[0]
    assert "2024-06-01T00:00:00Z" in seen_params[0]["$filter"]


@pytest.mark.asyncio
async def test_outlook_scan_cross_folder_dedup_by_message_id(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    scanner = OutlookScanner()
    account = EmailAccount(id=6, name="outlook", type="outlook", username="client-id", oauth_token_path=str(tmp_path / "cache.json"))

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    duplicate_msg = {"id": "g1", "internetMessageId": "uid-dup", "subject": "Dup", "bodyPreview": "", "from": {"emailAddress": {"address": "a@test"}}, "receivedDateTime": "2024-01-01T00:00:00Z", "hasAttachments": False}
    folder_a = {"id": "f-a", "displayName": "Folder A", "wellKnownName": None, "totalItemCount": 1, "childFolderCount": 0}
    folder_b = {"id": "f-b", "displayName": "Folder B", "wellKnownName": None, "totalItemCount": 1, "childFolderCount": 0}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

        async def get(self, url, headers=None, params=None):
            del headers, params
            if "/mailFolders" in url and "/messages" not in url:
                return FakeResponse({"value": [folder_a, folder_b], "@odata.nextLink": None})
            return FakeResponse({"value": [duplicate_msg], "@odata.nextLink": None})

    monkeypatch.setattr(email_scanner.httpx, "AsyncClient", lambda timeout: FakeClient())
    monkeypatch.setattr(scanner, "_acquire_access_token", AsyncMock(return_value="token"))

    emails = await scanner.scan(account, last_uid=None)
    assert len(emails) == 1
    assert emails[0].uid == "uid-dup"


@pytest.mark.asyncio
async def test_outlook_scan_first_pass_fetches_all_available_pages(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    scanner = OutlookScanner()
    account = EmailAccount(id=11, name="outlook", type="outlook", username="client-id", oauth_token_path=str(tmp_path / "cache.json"))
    folder = {"id": "f-inbox", "displayName": "Inbox", "wellKnownName": "inbox", "totalItemCount": 6, "childFolderCount": 0}
    page_one = [
        {"id": f"id-{idx}", "internetMessageId": f"uid-{idx}", "subject": "Invoice", "bodyPreview": "", "from": {"emailAddress": {"address": "a@test"}}, "receivedDateTime": f"2024-01-{idx+1:02d}T00:00:00Z"}
        for idx in range(3)
    ]
    page_two = [
        {"id": f"id-{idx}", "internetMessageId": f"uid-{idx}", "subject": "Invoice", "bodyPreview": "", "from": {"emailAddress": {"address": "a@test"}}, "receivedDateTime": f"2024-02-{idx-2:02d}T00:00:00Z"}
        for idx in range(3, 6)
    ]
    urls: list[str] = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

        async def get(self, url, headers=None, params=None):
            del headers, params
            urls.append(url)
            if "/mailFolders" in url and "/messages" not in url:
                return FakeResponse({"value": [folder], "@odata.nextLink": None})
            if "/messages" in url and url.endswith("/messages"):
                return FakeResponse({"value": page_one, "@odata.nextLink": f"{email_scanner.GRAPH_BASE_URL}/me/mailFolders/f-inbox/messages?next=2"})
            return FakeResponse({"value": page_two, "@odata.nextLink": None})

    monkeypatch.setattr(email_scanner.httpx, "AsyncClient", lambda timeout: FakeClient())
    monkeypatch.setattr(scanner, "_acquire_access_token", AsyncMock(return_value="token"))

    emails = await scanner.scan(account, last_uid=None)
    assert len(emails) == 6
    msg_urls = [u for u in urls if "/messages" in u]
    assert len(msg_urls) == 2


@pytest.mark.asyncio
async def test_outlook_scan_first_pass_respects_configured_limit(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(email_scanner, "FIRST_SCAN_LIMIT", 2)
    scanner = OutlookScanner()
    account = EmailAccount(id=12, name="outlook", type="outlook", username="client-id", oauth_token_path=str(tmp_path / "cache.json"))
    folder = {"id": "f-inbox", "displayName": "Inbox", "wellKnownName": "inbox", "totalItemCount": 5, "childFolderCount": 0}
    page = [
        {"id": f"id-{idx}", "internetMessageId": f"uid-{idx}", "subject": "Invoice", "bodyPreview": "", "from": {"emailAddress": {"address": "a@test"}}, "receivedDateTime": f"2024-01-{idx+1:02d}T00:00:00Z"}
        for idx in range(5)
    ]
    urls: list[str] = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

        async def get(self, url, headers=None, params=None):
            del headers, params
            urls.append(url)
            if "/mailFolders" in url and "/messages" not in url:
                return FakeResponse({"value": [folder], "@odata.nextLink": None})
            return FakeResponse({"value": page, "@odata.nextLink": "https://graph.microsoft.com/next"})

    monkeypatch.setattr(email_scanner.httpx, "AsyncClient", lambda timeout: FakeClient())
    monkeypatch.setattr(scanner, "_acquire_access_token", AsyncMock(return_value="token"))

    emails = await scanner.scan(account, last_uid=None)
    assert len(emails) == 2
    assert urls == [
        f"{email_scanner.GRAPH_BASE_URL}/me/mailFolders?$top=100&includeHiddenFolders=true",
        f"{email_scanner.GRAPH_BASE_URL}/me/mailFolders/f-inbox/messages",
    ]


@pytest.mark.asyncio
async def test_outlook_access_token_and_attachment_helpers(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    scanner = OutlookScanner()
    account = EmailAccount(id=9, name="o", type="outlook", username="client-id", oauth_token_path=str(tmp_path / "cache.json"))

    class Loop:
        async def run_in_executor(self, executor, func):
            del executor
            return func()

    monkeypatch.setattr(email_scanner.asyncio, "get_running_loop", lambda: Loop())
    monkeypatch.setattr(scanner, "_acquire_token_sync", lambda account: {"access_token": "abc"})
    assert await scanner._acquire_access_token(account) == "abc"

    monkeypatch.setattr(scanner, "_acquire_token_sync", lambda account: {"error_description": "bad"})
    with pytest.raises(RuntimeError, match="Outlook auth failed"):
        await scanner._acquire_access_token(account)

    class AttachmentClient:
        async def get(self, *args, **kwargs):
            del args, kwargs
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {
                    "value": [
                        {
                            "@odata.type": "#microsoft.graph.fileAttachment",
                            "name": None,
                            "contentBytes": base64.b64encode(b"pdf").decode(),
                            "contentType": None,
                        },
                        {"@odata.type": "#microsoft.graph.fileAttachment", "name": "skip.pdf", "contentBytes": None},
                    ]
                },
            )

    attachments = await scanner._fetch_attachments(AttachmentClient(), {"Authorization": "x"}, "message-id")
    assert attachments == []
    assert await scanner._fetch_attachments(AttachmentClient(), {}, "") == []


def test_outlook_token_cache_and_sync_flow(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    scanner = OutlookScanner()
    token_path = tmp_path / "oauth" / "cache.json"
    account = EmailAccount(id=5, name="outlook", type="outlook", username="client", oauth_token_path=str(token_path))
    token_path.parent.mkdir(parents=True, exist_ok=True)

    class FakeCache:
        def __init__(self):
            self.has_state_changed = True
            self.loaded = None

        def deserialize(self, text):
            self.loaded = text

        def serialize(self):
            return "cached"

    class FakeApp:
        def __init__(self, client_id, authority, token_cache):
            self.client_id = client_id
            self.authority = authority
            self.token_cache = token_cache

        def get_accounts(self):
            return ["acct"]

        def acquire_token_silent(self, scopes, account):
            del scopes, account
            return {"access_token": "silent-token"}

    monkeypatch.setattr(email_scanner.msal, "SerializableTokenCache", FakeCache)
    monkeypatch.setattr(email_scanner.msal, "PublicClientApplication", FakeApp)
    token_path.write_text("persisted", encoding="utf-8")
    cache = scanner._load_cache(account)
    assert isinstance(cache, FakeCache)
    assert cache.loaded == "persisted"
    scanner._save_cache(account, cache)
    assert token_path.read_text(encoding="utf-8") == "cached"
    result = scanner._acquire_token_sync(account)
    assert result["access_token"] == "silent-token"

    class DeviceFlowApp(FakeApp):
        def get_accounts(self):
            return []

        def initiate_device_flow(self, scopes):
            del scopes
            return {"verification_uri": "https://login", "user_code": "123"}

        def acquire_token_by_device_flow(self, flow):
            del flow
            return {"access_token": "device-token"}

    monkeypatch.setattr(email_scanner.msal, "PublicClientApplication", DeviceFlowApp)
    initiated = scanner._initiate_device_flow_sync(account)
    assert initiated["user_code"] == "123"
    assert scanner._complete_device_flow_sync(account, initiated)["access_token"] == "device-token"

    class BrokenDeviceFlowApp(DeviceFlowApp):
        def initiate_device_flow(self, scopes):
            del scopes
            return {}

    monkeypatch.setattr(email_scanner.msal, "PublicClientApplication", BrokenDeviceFlowApp)
    with pytest.raises(RuntimeError, match="device flow"):
        scanner._initiate_device_flow_sync(account)

    class NoSilentApp(FakeApp):
        def get_accounts(self):
            return []

    monkeypatch.setattr(email_scanner.msal, "PublicClientApplication", NoSilentApp)
    with pytest.raises(RuntimeError, match="Outlook authorization required"):
        scanner._acquire_token_sync(account)

    no_path_account = EmailAccount(id=6, name="o", type="outlook", username="client", oauth_token_path=None)
    assert scanner._load_cache(no_path_account).__class__ is FakeCache
    cache = FakeCache()
    cache.has_state_changed = False
    scanner._save_cache(no_path_account, cache)
    scanner._save_cache(account, cache)

    missing_path_account = EmailAccount(id=7, name="o", type="outlook", username="client", oauth_token_path=str(tmp_path / "missing" / "cache.json"))
    assert scanner._load_cache(missing_path_account).__class__ is FakeCache


@pytest.mark.asyncio
async def test_outlook_has_cached_token_and_async_device_flow_helpers(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    scanner = OutlookScanner()
    account = EmailAccount(id=10, name="o", type="outlook", username="client-id", oauth_token_path=str(tmp_path / "cache.json"))

    class Loop:
        async def run_in_executor(self, executor, func):
            del executor
            return func()

    monkeypatch.setattr(email_scanner.asyncio, "get_running_loop", lambda: Loop())
    monkeypatch.setattr(scanner, "_acquire_access_token", AsyncMock(return_value="abc"))
    assert await scanner.has_cached_token_async(account) is True
    monkeypatch.setattr(scanner, "_acquire_access_token", AsyncMock(side_effect=RuntimeError("missing")))
    assert await scanner.has_cached_token_async(account) is False

    monkeypatch.setattr(scanner, "_initiate_device_flow_sync", lambda account: {"user_code": "XYZ", "verification_uri": "https://microsoft.com/devicelogin"})
    monkeypatch.setattr(scanner, "_complete_device_flow_sync", lambda account, flow: {"access_token": "done"})
    assert (await scanner.initiate_device_flow_async(account))["user_code"] == "XYZ"
    assert (await scanner.complete_device_flow_async(account, {"user_code": "XYZ"}))["access_token"] == "done"


def test_oauth_flow_registry_get_set_remove_and_expiry() -> None:
    registry = OAuthFlowRegistry()
    state = OAuthFlowState(status="pending")
    registry.set(1, state)
    assert registry.get(1) is state
    registry.remove(1)
    assert registry.get(1) is None

    expired_state = OAuthFlowState(
        status="pending",
        expires_at=datetime.now(timezone.utc) - email_scanner.timedelta(seconds=1),
    )
    registry.set(2, expired_state)
    loaded = registry.get(2)
    assert loaded is expired_state
    assert loaded.status == "expired"
    assert loaded.detail == "Device code expired"


def test_oauth_flow_registry_cancels_pending_tasks_on_expiry_and_remove() -> None:
    registry = OAuthFlowRegistry()

    class FakeTask:
        def __init__(self) -> None:
            self.cancelled = False

        def done(self) -> bool:
            return False

        def cancel(self) -> None:
            self.cancelled = True

    expiring_task = FakeTask()
    registry.set(
        1,
        OAuthFlowState(
            status="pending",
            expires_at=datetime.now(timezone.utc) - email_scanner.timedelta(seconds=1),
            task=expiring_task,
        ),
    )
    registry.get(1)
    assert expiring_task.cancelled is True

    remove_task = FakeTask()
    registry.set(2, OAuthFlowState(status="pending", task=remove_task))
    registry.remove(2)
    assert remove_task.cancelled is True


def test_scanner_factory_routes_and_rejects_unknown() -> None:
    assert isinstance(ScannerFactory.get_scanner("imap"), ImapScanner)
    assert isinstance(ScannerFactory.get_scanner("qq"), ImapScanner)
    assert isinstance(ScannerFactory.get_scanner("pop3"), Pop3Scanner)
    assert isinstance(ScannerFactory.get_scanner("outlook"), OutlookScanner)
    with pytest.raises(ValueError, match="Unknown email account type"):
        ScannerFactory.get_scanner("smtp")


def test_get_outlook_msal_params_from_type(settings) -> None:
    personal_id, personal_auth = email_scanner._get_outlook_msal_params_from_type("personal")
    assert personal_auth == "https://login.microsoftonline.com/consumers"
    assert personal_id == settings.OUTLOOK_PERSONAL_CLIENT_ID

    org_id, org_auth = email_scanner._get_outlook_msal_params_from_type("organizational")
    assert org_auth == "https://login.microsoftonline.com/common"
    assert org_id == settings.OUTLOOK_AAD_CLIENT_ID


@pytest.mark.asyncio
async def test_complete_device_flow_with_path_writes_token(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    scanner = OutlookScanner()
    token_path = str(tmp_path / "token.json")
    flow = {"device_code": "dc", "interval": 5, "expires_in": 900}

    class FakeApp:
        def __init__(self, **kwargs):
            pass

        def acquire_token_by_device_flow(self, flow):
            del flow
            return {"access_token": "tok123", "token_type": "Bearer"}

    class FakeCache:
        has_state_changed = True
        def __init__(self):
            pass
        def deserialize(self, _):
            pass
        def serialize(self):
            return '{"cached": true}'

    monkeypatch.setattr(email_scanner.msal, "PublicClientApplication", lambda **kwargs: FakeApp())
    monkeypatch.setattr(email_scanner.msal, "SerializableTokenCache", FakeCache)

    result = scanner._complete_device_flow_with_path_sync(flow, token_path, "personal")
    assert result["access_token"] == "tok123"
    assert (tmp_path / 'token.json').read_text() == '{"cached": true}'


@pytest.mark.asyncio
async def test_complete_device_flow_with_path_no_token_path(monkeypatch: pytest.MonkeyPatch) -> None:
    scanner = OutlookScanner()

    class FakeApp:
        def __init__(self, **kwargs):
            pass

        def acquire_token_by_device_flow(self, flow):
            del flow
            return {"error": "expired"}

    class FakeCache:
        has_state_changed = False
        def __init__(self):
            pass
        def deserialize(self, _):
            pass
        def serialize(self):
            return "{}"

    monkeypatch.setattr(email_scanner.msal, "PublicClientApplication", lambda **kwargs: FakeApp())
    monkeypatch.setattr(email_scanner.msal, "SerializableTokenCache", FakeCache)

    result = await scanner.complete_device_flow_async_with_path({}, None, "personal")
    assert result["error"] == "expired"


@pytest.mark.asyncio
async def test_acquire_token_sync_raises_when_no_token_path() -> None:
    scanner = OutlookScanner()
    account = email_scanner.EmailAccount(id=99, name="no-path", type="outlook", username="a@b.com", oauth_token_path=None)
    with pytest.raises(RuntimeError, match="Outlook authorization required"):
        scanner._acquire_token_sync(account)


@pytest.mark.asyncio
async def test_pop3_hydrate_email_default_is_noop(settings) -> None:
    scanner = email_scanner.Pop3Scanner()
    account = EmailAccount(
        id=10, name="pop", type="pop3", host="pop.example.com", port=995,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    email = email_scanner.RawEmail(
        uid="1", subject="s", body_text="hi", body_html="", from_addr="a",
        received_at=datetime.now(timezone.utc), is_hydrated=True,
    )
    assert await scanner.hydrate_email(account, email) is email


@pytest.mark.asyncio
async def test_outlook_hydrate_email_returns_early_if_hydrated(settings) -> None:
    scanner = OutlookScanner()
    account = EmailAccount(id=11, name="o", type="outlook", username="client-id")
    email = email_scanner.RawEmail(
        uid="1", subject="s", body_text="body", body_html="", from_addr="a",
        received_at=datetime.now(timezone.utc), is_hydrated=True,
    )
    assert await scanner.hydrate_email(account, email) is email


@pytest.mark.asyncio
async def test_outlook_hydrate_email_returns_early_if_graph_id_missing(settings) -> None:
    scanner = OutlookScanner()
    account = EmailAccount(id=11, name="o", type="outlook", username="client-id")
    email = email_scanner.RawEmail(
        uid="1", subject="s", body_text="", body_html="", from_addr="a",
        received_at=datetime.now(timezone.utc), is_hydrated=False, headers={},
    )
    result = await scanner.hydrate_email(account, email)
    assert result.is_hydrated is True
    assert result.body_text == ""


@pytest.mark.asyncio
async def test_outlook_hydrate_email_handles_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    scanner = OutlookScanner()
    account = EmailAccount(id=11, name="o", type="outlook", username="client-id")
    email = email_scanner.RawEmail(
        uid="1", subject="s", body_text="", body_html="", from_addr="a",
        received_at=datetime.now(timezone.utc), is_hydrated=False,
        headers={"_graph_id": "g1", "_has_attachments": "True"},
    )

    class FailingClient:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

        async def get(self, url, headers=None, params=None):
            del url, headers, params
            raise httpx.HTTPError("graph down")

    monkeypatch.setattr(scanner, "_acquire_access_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(email_scanner.httpx, "AsyncClient", FailingClient)
    warnings: list[str] = []
    monkeypatch.setattr(email_scanner.logger, "warning", lambda m, *args: warnings.append(m % args))

    result = await scanner.hydrate_email(account, email)
    assert result.is_hydrated is True
    assert any("Outlook hydrate failed" in w for w in warnings)


@pytest.mark.asyncio
async def test_outlook_hydrate_email_without_attachments_skips_attachments_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    scanner = OutlookScanner()
    account = EmailAccount(id=11, name="o", type="outlook", username="client-id")
    email = email_scanner.RawEmail(
        uid="1", subject="s", body_text="", body_html="", from_addr="a",
        received_at=datetime.now(timezone.utc), is_hydrated=False,
        headers={"_graph_id": "g1", "_has_attachments": "False"},
    )
    calls: list[str] = []

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"body": {"contentType": "text", "content": "text body"}}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

        async def get(self, url, headers=None, params=None):
            del headers, params
            calls.append(url)
            return FakeResp()

    monkeypatch.setattr(scanner, "_acquire_access_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(email_scanner.httpx, "AsyncClient", FakeClient)

    result = await scanner.hydrate_email(account, email)
    assert result.is_hydrated is True
    assert result.body_text == "text body"
    assert all("/attachments" not in c for c in calls)


def test_parse_state_helpers_all_branches() -> None:
    from app.services.email_scanner import (
        _parse_imap_state,
        _serialize_imap_state,
        _parse_graph_state,
        _serialize_graph_state,
        _imap_folder_should_scan,
    )

    raw = json.dumps({"INBOX": {"uid": "5", "uidvalidity": "12345"}, "Archive": {"uid": "2", "uidvalidity": "99"}})
    result = _parse_imap_state(raw)
    assert result["INBOX"] == {"uid": "5", "uidvalidity": "12345", "uidnext": "", "messages": ""}
    assert result["Archive"] == {"uid": "2", "uidvalidity": "99", "uidnext": "", "messages": ""}

    assert _parse_imap_state("notjson") == {"INBOX": {"uid": "notjson", "uidvalidity": "", "uidnext": "", "messages": ""}}

    assert _parse_imap_state(json.dumps(["a", "b"])) == {"INBOX": {"uid": '["a", "b"]', "uidvalidity": "", "uidnext": "", "messages": ""}}

    assert _parse_imap_state(None) == {}

    # _serialize_imap_state: round-trips
    state = {"INBOX": {"uid": "1", "uidvalidity": "999"}}
    assert json.loads(_serialize_imap_state(state)) == state

    assert _parse_graph_state(json.dumps([1, 2, 3])) == {"__legacy_uid__": "[1, 2, 3]"}

    raw_g = json.dumps({"f-inbox": "2024-01-01T00:00:00Z"})
    assert _parse_graph_state(raw_g) == {"f-inbox": "2024-01-01T00:00:00Z"}

    # _parse_graph_state: legacy bare string
    assert _parse_graph_state("uid-xyz") == {"__legacy_uid__": "uid-xyz"}

    # _parse_graph_state: invalid JSON -> legacy
    assert _parse_graph_state("{broken}") == {"__legacy_uid__": "{broken}"}

    # _parse_graph_state: None -> empty
    assert _parse_graph_state(None) == {}

    # _serialize_graph_state: round-trips
    state_g = {"f-inbox": "2024-06-01T00:00:00Z"}
    assert json.loads(_serialize_graph_state(state_g)) == state_g

    # _imap_folder_should_scan: skip flags
    assert _imap_folder_should_scan(("\\Noselect", "\\HasChildren")) is False
    assert _imap_folder_should_scan(("\\Drafts",)) is False
    assert _imap_folder_should_scan(("\\Trash",)) is False
    assert _imap_folder_should_scan(("\\All",)) is False

    # _imap_folder_should_scan: include flags
    assert _imap_folder_should_scan(()) is True
    assert _imap_folder_should_scan(("\\HasNoChildren",)) is True


def test_imap_scan_multi_folder_and_cross_folder_dedup(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    from app.services.email_scanner import ImapScanner, _parse_imap_state
    from app.services import email_scanner

    shared_msg_id = "<shared@example.com>"
    inbox_msg = SimpleNamespace(uid="10", subject="inv", text="", html="", from_="a@test", date="2024-01-01T00:00:00Z", attachments=[], headers={"Message-ID": shared_msg_id})
    archive_msg = SimpleNamespace(uid="20", subject="inv-dup", text="", html="", from_="b@test", date="2024-01-01T00:00:00Z", attachments=[], headers={"Message-ID": shared_msg_id})
    unique_archive_msg = SimpleNamespace(uid="21", subject="unique", text="", html="", from_="c@test", date="2024-02-01T00:00:00Z", attachments=[], headers={"Message-ID": "<unique@example.com>"})
    drafts_msg = SimpleNamespace(uid="7", subject="draft", text="", html="", from_="d@test", date="2024-01-01T00:00:00Z", attachments=[], headers={})

    folders = [
        _FakeFolderInfo("INBOX", ()),
        _FakeFolderInfo("Archive", ()),
        _FakeFolderInfo("Drafts", ("\\Drafts",)),
    ]

    class MultiFolderMailbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self.folder = _FakeFolderManager(folders=folders)

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                return _FakeContextManager(self)

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            folder_name = self.folder.current
            if folder_name == "INBOX":
                return [inbox_msg]
            if folder_name == "Archive":
                return [archive_msg, unique_archive_msg]
            return [drafts_msg]

    monkeypatch.setattr(email_scanner, "MailBox", MultiFolderMailbox)
    account = EmailAccount(
        id=1, name="imap", type="imap", host="imap.example.com", port=993,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    scanner = ImapScanner()
    emails = scanner._scan_sync(account, None)

    assert len(emails) == 2
    assert {e.uid for e in emails} == {"10", "21"}
    subjects = {e.subject for e in emails}
    assert "inv-dup" not in subjects

    state = _parse_imap_state(scanner._last_scan_state)
    assert "INBOX" in state
    assert "Archive" in state
    assert "Drafts" not in state


def test_imap_scan_uidvalidity_change_resets_folder(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    from app.services.email_scanner import ImapScanner, _serialize_imap_state
    from app.services import email_scanner

    new_msg = SimpleNamespace(uid="1", subject="fresh", text="", html="", from_="a@test", date="2024-06-01T00:00:00Z", attachments=[], headers={})

    class ChangeValidityMailbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self.folder = _FakeFolderManager(folders=[_FakeFolderInfo("INBOX")], uidvalidity_map={"INBOX": 99999})

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                return _FakeContextManager(self)

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            return [new_msg]

    monkeypatch.setattr(email_scanner, "MailBox", ChangeValidityMailbox)
    account = EmailAccount(
        id=1, name="imap", type="imap", host="imap.example.com", port=993,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    old_state = _serialize_imap_state({"INBOX": {"uid": "999", "uidvalidity": "11111"}})
    scanner = ImapScanner()
    emails = scanner._scan_sync(account, old_state)

    assert len(emails) == 1
    assert emails[0].uid == "1"


def test_imap_scan_folder_set_failure_skips_folder(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    from app.services.email_scanner import ImapScanner
    from app.services import email_scanner
    from imap_tools.errors import MailboxLoginError

    class FolderSetFailMailbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self.folder = _FakeFolderManager(folders=[
                    _FakeFolderInfo("INBOX"),
                    _FakeFolderInfo("BadFolder"),
                ])
                self._set_count = 0

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                return _FakeContextManager(self)

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            return []

    class BadFolderManager(_FakeFolderManager):
        def set(self, name):
            if name == "BadFolder":
                raise MailboxLoginError("INBOX", 403)
            super().set(name)

    FolderSetFailMailbox.folder = property(lambda self: self._folder)

    class FSFMailbox2:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self._folder = BadFolderManager(folders=[
                    _FakeFolderInfo("INBOX"),
                    _FakeFolderInfo("BadFolder"),
                ])
        @property
        def folder(self):
            return self._folder

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                return _FakeContextManager(self)

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            return []

    monkeypatch.setattr(email_scanner, "MailBox", FSFMailbox2)
    account = EmailAccount(
        id=1, name="imap", type="imap", host="imap.example.com", port=993,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    emails = ImapScanner()._scan_sync(account, None)
    assert isinstance(emails, list)


def test_imap_scan_fetch_failure_skips_folder(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    from app.services.email_scanner import ImapScanner
    from app.services import email_scanner
    from imap_tools.errors import MailboxLoginError

    class FetchFailMailbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self._folder = _FakeFolderManager(folders=[_FakeFolderInfo("INBOX")])

        @property
        def folder(self):
            return self._folder

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                return _FakeContextManager(self)

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            raise MailboxLoginError("INBOX", 403)

    monkeypatch.setattr(email_scanner, "MailBox", FetchFailMailbox)
    account = EmailAccount(
        id=1, name="imap", type="imap", host="imap.example.com", port=993,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    emails = ImapScanner()._scan_sync(account, None)
    assert emails == []


@pytest.mark.asyncio
async def test_imap_hydrate_selects_correct_folder(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    from app.services.email_scanner import ImapScanner, RawEmail
    from app.services import email_scanner

    selected: list[str] = []
    msg = SimpleNamespace(uid="5", text="body", html="", attachments=[], headers={})

    class HydrateFolderMailbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self._folder = _FakeFolderManager()

        @property
        def folder(self):
            return self._folder

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                return _FakeContextManager(self)

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            return [msg]

    class HydrateFolderManager(_FakeFolderManager):
        def set(self, name):
            selected.append(name)
            super().set(name)

    HydrateFolderMailbox._folder = HydrateFolderManager()

    class HFM2:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self._folder = HydrateFolderManager()

        @property
        def folder(self):
            return self._folder

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                return _FakeContextManager(self)

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            return [msg]

    monkeypatch.setattr(email_scanner, "AND", lambda **kwargs: kwargs)
    monkeypatch.setattr(email_scanner, "AND", lambda **kwargs: kwargs)
    monkeypatch.setattr(email_scanner, "MailBox", HFM2)
    account = EmailAccount(
        id=1, name="imap", type="imap", host="imap.example.com", port=993,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    email = RawEmail(uid="5", subject="", body_text="", body_html="", from_addr="", received_at=datetime.now(timezone.utc), is_hydrated=False, folder="Archive")
    result = await ImapScanner().hydrate_email(account, email)
    assert result.is_hydrated is True
    assert "Archive" in selected


@pytest.mark.asyncio
async def test_outlook_should_skip_folder_all_cases(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.email_scanner import OutlookScanner
    scanner = OutlookScanner()
    assert scanner._should_skip_folder({"wellKnownName": "drafts"}) is True
    assert scanner._should_skip_folder({"wellKnownName": "deleteditems"}) is True
    assert scanner._should_skip_folder({"wellKnownName": "outbox"}) is True
    assert scanner._should_skip_folder({"@odata.type": "#microsoft.graph.mailSearchFolder"}) is True
    assert scanner._should_skip_folder({"totalItemCount": 0}) is True
    assert scanner._should_skip_folder({"wellKnownName": "inbox", "totalItemCount": 5}) is False
    assert scanner._should_skip_folder({"wellKnownName": None, "totalItemCount": 1}) is False
    assert scanner._should_skip_folder({}) is False


@pytest.mark.asyncio
async def test_outlook_iter_mail_folders_recursive_and_dedup(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from app.services.email_scanner import OutlookScanner
    scanner = OutlookScanner()

    class FakeResp:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            return None

        def json(self):
            return self._data

    root_folder = {"id": "f-root", "displayName": "Inbox", "childFolderCount": 1}
    child_folder = {"id": "f-child", "displayName": "Archive", "childFolderCount": 0}
    urls_seen: list[str] = []

    class FakeClient:
        async def get(self, url, headers=None):
            del headers
            urls_seen.append(url)
            if "f-root" in url and "childFolders" in url:
                return FakeResp({"value": [child_folder], "@odata.nextLink": None})
            if "mailFolders?" in url:
                return FakeResp({"value": [root_folder], "@odata.nextLink": None})
            return FakeResp({"value": [], "@odata.nextLink": None})

    folders = [f async for f in scanner._iter_mail_folders(FakeClient(), {})]
    assert len(folders) == 2
    assert {f["id"] for f in folders} == {"f-root", "f-child"}
    root_count = sum(1 for u in urls_seen if "mailFolders?" in u)
    assert root_count == 1


def test_imap_scan_status_exception_continues(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    from app.services.email_scanner import ImapScanner
    from app.services import email_scanner

    class StatusErrorManager(_FakeFolderManager):
        def status(self, name, items):
            raise OSError("no status")

    class StatusErrMailbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self._folder = StatusErrorManager(folders=[_FakeFolderInfo("INBOX")])

        @property
        def folder(self):
            return self._folder

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                return _FakeContextManager(self)

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            return []

    monkeypatch.setattr(email_scanner, "MailBox", StatusErrMailbox)
    account = EmailAccount(
        id=1, name="imap", type="imap", host="imap.example.com", port=993,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    emails = ImapScanner()._scan_sync(account, None)
    assert isinstance(emails, list)


def test_imap_scan_uidvalidity_resets_effective_uid(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    from app.services.email_scanner import ImapScanner, _serialize_imap_state
    from app.services import email_scanner

    new_msg = SimpleNamespace(uid="1", subject="s", text="", html="", from_="a@test", date="2024-01-01T00:00:00Z", attachments=[], headers={})

    class ValidityChangedMBX:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self._folder = _FakeFolderManager(
                    folders=[_FakeFolderInfo("INBOX")],
                    uidvalidity_map={"INBOX": 77777},
                )

        @property
        def folder(self):
            return self._folder

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                return _FakeContextManager(self)

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, mark_seen, headers_only, bulk
            return [new_msg]

    monkeypatch.setattr(email_scanner, "MailBox", ValidityChangedMBX)
    account = EmailAccount(
        id=1, name="imap", type="imap", host="imap.example.com", port=993,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    old_state = _serialize_imap_state({"INBOX": {"uid": "999", "uidvalidity": "11111"}})
    scanner = ImapScanner()
    emails = scanner._scan_sync(account, old_state)
    assert len(emails) == 1


@pytest.mark.asyncio
async def test_outlook_scan_empty_folder_skipped_and_email_limit_and_highest_dt(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from app.services.email_scanner import OutlookScanner, GRAPH_BASE_URL
    import json as _json
    scanner = OutlookScanner()
    account = EmailAccount(id=7, name="o", type="outlook", username="c-id", oauth_token_path=str(tmp_path / "c.json"))
    monkeypatch.setattr(email_scanner, "FIRST_SCAN_LIMIT", 1)

    folder_inbox = {"id": "fi", "displayName": "Inbox", "wellKnownName": "inbox", "totalItemCount": 5, "childFolderCount": 0}
    folder_empty = {"id": "fe", "displayName": "Empty", "wellKnownName": None, "totalItemCount": 0, "childFolderCount": 0}
    msgs = [
        {"id": f"g{i}", "internetMessageId": f"uid-{i}", "subject": f"S{i}", "bodyPreview": "", "from": {"emailAddress": {"address": "a@test"}}, "receivedDateTime": f"2024-0{i}-01T00:00:00Z", "hasAttachments": False}
        for i in range(1, 6)
    ]

    class FakeResp:
        def __init__(self, d):
            self._d = d
        def raise_for_status(self): return None
        def json(self): return self._d

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None, params=None):
            del headers, params
            if "/mailFolders" in url and "/messages" not in url:
                return FakeResp({"value": [folder_inbox, folder_empty], "@odata.nextLink": None})
            return FakeResp({"value": msgs, "@odata.nextLink": None})

    monkeypatch.setattr(email_scanner.httpx, "AsyncClient", lambda timeout: FakeClient())
    monkeypatch.setattr(scanner, "_acquire_access_token", AsyncMock(return_value="tok"))

    emails = await scanner.scan(account, last_uid=None)
    assert len(emails) == 1
    state = _json.loads(scanner._last_scan_state)
    assert "fi" in state
    assert state["fi"] != ""
    assert "fe" not in state


@pytest.mark.asyncio
async def test_outlook_iter_folders_seen_url_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.email_scanner import OutlookScanner, GRAPH_BASE_URL
    scanner = OutlookScanner()

    folder = {"id": "f-child", "displayName": "X", "childFolderCount": 1}
    urls_seen: list[str] = []

    class FakeClient:
        async def get(self, url, headers=None):
            urls_seen.append(url)
            child_url = f"{GRAPH_BASE_URL}/me/mailFolders/f-child/childFolders?$top=100"
            if url == child_url:
                return type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: {"value": [], "@odata.nextLink": None}})()
            return type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: {"value": [folder], "@odata.nextLink": child_url}})()

    folders = [f async for f in scanner._iter_mail_folders(FakeClient(), {})]
    assert urls_seen.count(f"{GRAPH_BASE_URL}/me/mailFolders?$top=100&includeHiddenFolders=true") == 1


@pytest.mark.asyncio
async def test_imap_hydrate_folder_set_failure_returns_empty(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    from app.services.email_scanner import ImapScanner, RawEmail
    from app.services import email_scanner
    from imap_tools.errors import MailboxLoginError

    class SetErrorFolderManager(_FakeFolderManager):
        def set(self, name):
            raise MailboxLoginError("folder_set_error", 403)

    class SetErrorMailbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self._folder = SetErrorFolderManager()

        @property
        def folder(self):
            return self._folder

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                return _FakeContextManager(self)

    monkeypatch.setattr(email_scanner, "AND", lambda **kwargs: kwargs)
    monkeypatch.setattr(email_scanner, "MailBox", SetErrorMailbox)
    account = EmailAccount(
        id=1, name="imap", type="imap", host="imap.example.com", port=993,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    email = RawEmail(uid="5", subject="", body_text="", body_html="", from_addr="", received_at=datetime.now(timezone.utc), is_hydrated=False, folder="Archive")
    result = await ImapScanner().hydrate_email(account, email)
    assert result.is_hydrated is True
    assert result.body_text == ""


def test_imap_scan_empty_uid_msg_skips_uid_update(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    from app.services.email_scanner import ImapScanner
    from app.services import email_scanner

    msg_no_uid = SimpleNamespace(uid="", subject="s", text="", html="", from_="a@test", date="2024-01-01T00:00:00Z", attachments=[], headers={})
    msg_with_uid = SimpleNamespace(uid="5", subject="s2", text="", html="", from_="a@test", date="2024-02-01T00:00:00Z", attachments=[], headers={})

    class MixedUidMailbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self.folder = _FakeFolderManager()

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                return _FakeContextManager(self)

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            return [msg_no_uid, msg_with_uid]

    monkeypatch.setattr(email_scanner, "MailBox", MixedUidMailbox)
    account = EmailAccount(
        id=1, name="imap", type="imap", host="imap.example.com", port=993,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    emails = ImapScanner()._scan_sync(account, None)
    assert any(e.uid == "5" for e in emails)


@pytest.mark.asyncio
async def test_outlook_scan_folder_with_no_id_skipped(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from app.services.email_scanner import OutlookScanner
    scanner = OutlookScanner()
    account = EmailAccount(id=8, name="o", type="outlook", username="c-id", oauth_token_path=str(tmp_path / "c.json"))

    folder_with_id = {"id": "f-ok", "displayName": "OK", "wellKnownName": "inbox", "totalItemCount": 1, "childFolderCount": 0}
    folder_no_id = {"id": None, "displayName": "NoId", "wellKnownName": None, "totalItemCount": 1, "childFolderCount": 0}

    class FakeResp:
        def __init__(self, d):
            self._d = d
        def raise_for_status(self): return None
        def json(self): return self._d

    calls: list[str] = []

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None, params=None):
            del headers, params
            calls.append(url)
            if "/mailFolders" in url and "/messages" not in url:
                return FakeResp({"value": [folder_with_id, folder_no_id], "@odata.nextLink": None})
            return FakeResp({"value": [], "@odata.nextLink": None})

    monkeypatch.setattr(email_scanner.httpx, "AsyncClient", lambda timeout: FakeClient())
    monkeypatch.setattr(scanner, "_acquire_access_token", AsyncMock(return_value="tok"))

    emails = await scanner.scan(account, last_uid=None)
    assert emails == []
    assert all("None" not in c for c in calls if "/messages" in c)


@pytest.mark.asyncio
async def test_outlook_scan_cross_folder_dedup_message_uid_in_seen(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from app.services.email_scanner import OutlookScanner
    scanner = OutlookScanner()
    account = EmailAccount(id=9, name="o", type="outlook", username="c-id", oauth_token_path=str(tmp_path / "c.json"))

    folder_a = {"id": "fa", "displayName": "Fa", "wellKnownName": None, "totalItemCount": 2, "childFolderCount": 0}
    folder_b = {"id": "fb", "displayName": "Fb", "wellKnownName": None, "totalItemCount": 2, "childFolderCount": 0}
    dup_msg = {"id": "g-dup", "internetMessageId": "uid-dup", "subject": "Dup", "bodyPreview": "", "from": {"emailAddress": {"address": "a@test"}}, "receivedDateTime": "2024-01-01T00:00:00Z", "hasAttachments": False}

    class FakeResp:
        def __init__(self, d):
            self._d = d
        def raise_for_status(self): return None
        def json(self): return self._d

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None, params=None):
            del headers, params
            if "/mailFolders" in url and "/messages" not in url:
                return FakeResp({"value": [folder_a, folder_b], "@odata.nextLink": None})
            return FakeResp({"value": [dup_msg], "@odata.nextLink": None})

    monkeypatch.setattr(email_scanner.httpx, "AsyncClient", lambda timeout: FakeClient())
    monkeypatch.setattr(scanner, "_acquire_access_token", AsyncMock(return_value="tok"))

    emails = await scanner.scan(account, last_uid=None)
    assert len(emails) == 1


@pytest.mark.asyncio
async def test_outlook_highest_dt_not_updated_for_older_messages(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from app.services.email_scanner import OutlookScanner
    import json as _json
    scanner = OutlookScanner()
    account = EmailAccount(id=10, name="o", type="outlook", username="c-id", oauth_token_path=str(tmp_path / "c.json"))

    folder = {"id": "fi", "displayName": "Inbox", "wellKnownName": "inbox", "totalItemCount": 2, "childFolderCount": 0}
    msgs = [
        {"id": "g1", "internetMessageId": "uid-1", "subject": "S1", "bodyPreview": "", "from": {"emailAddress": {"address": "a@test"}}, "receivedDateTime": "2024-06-01T00:00:00Z", "hasAttachments": False},
        {"id": "g2", "internetMessageId": "uid-2", "subject": "S2", "bodyPreview": "", "from": {"emailAddress": {"address": "a@test"}}, "receivedDateTime": "2024-05-01T00:00:00Z", "hasAttachments": False},
    ]

    class FakeResp:
        def __init__(self, d):
            self._d = d
        def raise_for_status(self): return None
        def json(self): return self._d

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None, params=None):
            del headers, params
            if "/mailFolders" in url and "/messages" not in url:
                return FakeResp({"value": [folder], "@odata.nextLink": None})
            return FakeResp({"value": msgs, "@odata.nextLink": None})

    monkeypatch.setattr(email_scanner.httpx, "AsyncClient", lambda timeout: FakeClient())
    monkeypatch.setattr(scanner, "_acquire_access_token", AsyncMock(return_value="tok"))

    await scanner.scan(account, last_uid=None)
    state = _json.loads(scanner._last_scan_state)
    assert state["fi"] == "2024-06-01T00:00:00Z"


def test_imap_dedup_uid_update_when_dup_has_higher_uid(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    from app.services.email_scanner import ImapScanner, _parse_imap_state
    from app.services import email_scanner

    shared_id = "<dup@test.com>"
    first_msg = SimpleNamespace(uid="1", subject="first", text="", html="", from_="a@test", date="2024-01-01T00:00:00Z", attachments=[], headers={"Message-ID": shared_id})
    dup_higher_uid = SimpleNamespace(uid="999", subject="dup", text="", html="", from_="b@test", date="2024-02-01T00:00:00Z", attachments=[], headers={"Message-ID": shared_id})
    second_unique = SimpleNamespace(uid="2", subject="unique", text="", html="", from_="c@test", date="2024-03-01T00:00:00Z", attachments=[], headers={"Message-ID": "<other@test.com>"})

    folders_list = [
        _FakeFolderInfo("INBOX", ()),
        _FakeFolderInfo("Junk", ()),
    ]

    class DedupMBX:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self._folder = _FakeFolderManager(folders=folders_list)

        @property
        def folder(self):
            return self._folder

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                return _FakeContextManager(self)

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            if self._folder.current == "INBOX":
                return [first_msg]
            return [dup_higher_uid, second_unique]

    monkeypatch.setattr(email_scanner, "MailBox", DedupMBX)
    account = EmailAccount(
        id=1, name="imap", type="imap", host="imap.example.com", port=993,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    scanner = ImapScanner()
    emails = scanner._scan_sync(account, None)
    assert {e.uid for e in emails} == {"1", "2"}
    state = _parse_imap_state(scanner._last_scan_state)
    assert state["Junk"]["uid"] == "999"


@pytest.mark.asyncio
async def test_outlook_iter_folders_child_url_dedup_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.email_scanner import OutlookScanner, GRAPH_BASE_URL
    scanner = OutlookScanner()

    child_url = f"{GRAPH_BASE_URL}/me/mailFolders/f-root/childFolders?$top=100"
    folder_with_child = {"id": "f-root", "displayName": "Root", "childFolderCount": 1}
    child_folder = {"id": "f-child", "displayName": "Child", "childFolderCount": 1}
    urls_hit: list[str] = []

    class FakeClient:
        async def get(self, url, headers=None):
            del headers
            urls_hit.append(url)
            if "f-child/childFolders" in url:
                return type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: {"value": [], "@odata.nextLink": None}})()
            if "f-root/childFolders" in url:
                return type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: {"value": [child_folder], "@odata.nextLink": child_url}})()
            return type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: {"value": [folder_with_child], "@odata.nextLink": None}})()

    folders = [f async for f in scanner._iter_mail_folders(FakeClient(), {})]
    assert {f["id"] for f in folders} == {"f-root", "f-child"}
    assert urls_hit.count(child_url) == 1


@pytest.mark.asyncio
async def test_outlook_scan_dedup_with_empty_message_uid(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from app.services.email_scanner import OutlookScanner
    scanner = OutlookScanner()
    account = EmailAccount(id=20, name="o", type="outlook", username="c-id", oauth_token_path=str(tmp_path / "c.json"))

    folder = {"id": "fi", "displayName": "I", "wellKnownName": "inbox", "totalItemCount": 2, "childFolderCount": 0}
    msgs = [
        {"id": None, "internetMessageId": None, "subject": "No UID", "bodyPreview": "", "from": {"emailAddress": {"address": "a@test"}}, "receivedDateTime": "2024-01-01T00:00:00Z", "hasAttachments": False},
        {"id": "g2", "internetMessageId": "uid-ok", "subject": "OK", "bodyPreview": "", "from": {"emailAddress": {"address": "b@test"}}, "receivedDateTime": "2024-02-01T00:00:00Z", "hasAttachments": False},
    ]

    class FakeResp:
        def __init__(self, d): self._d = d
        def raise_for_status(self): return None
        def json(self): return self._d

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None, params=None):
            del headers, params
            if "/messages" not in url:
                return FakeResp({"value": [folder], "@odata.nextLink": None})
            return FakeResp({"value": msgs, "@odata.nextLink": None})

    monkeypatch.setattr(email_scanner.httpx, "AsyncClient", lambda timeout: FakeClient())
    monkeypatch.setattr(scanner, "_acquire_access_token", AsyncMock(return_value="tok"))

    emails = await scanner.scan(account, last_uid=None)
    assert len(emails) == 2


def test_imap_dedup_skip_uid_update_when_dup_has_lower_uid(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    from app.services.email_scanner import ImapScanner
    from app.services import email_scanner

    shared_id = "<shared2@test.com>"
    unique_first = SimpleNamespace(uid="10", subject="u1", text="", html="", from_="a@test", date="2024-01-02T00:00:00Z", attachments=[], headers={"Message-ID": "<other2@test.com>"})
    dup_lower_uid = SimpleNamespace(uid="3", subject="dup", text="", html="", from_="b@test", date="2024-01-01T00:00:00Z", attachments=[], headers={"Message-ID": shared_id})

    class TwoMsgInbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self._folder = _FakeFolderManager(folders=[
                    _FakeFolderInfo("INBOX"),
                    _FakeFolderInfo("Archive"),
                ])
                self._inbox_done = False

        @property
        def folder(self):
            return self._folder

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                return _FakeContextManager(self)

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            if self._folder.current == "INBOX":
                return [
                    SimpleNamespace(uid="8", subject="s", text="", html="", from_="a@test", date="2024-01-03T00:00:00Z", attachments=[], headers={"Message-ID": shared_id}),
                    SimpleNamespace(uid="10", subject="u1", text="", html="", from_="a@test", date="2024-01-02T00:00:00Z", attachments=[], headers={"Message-ID": "<other2@test.com>"}),
                ]
            return [dup_lower_uid]

    monkeypatch.setattr(email_scanner, "MailBox", TwoMsgInbox)
    account = EmailAccount(
        id=1, name="imap", type="imap", host="imap.example.com", port=993,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    emails = ImapScanner()._scan_sync(account, None)
    assert all(e.uid != "3" for e in emails)


@pytest.mark.asyncio
async def test_outlook_iter_folders_child_url_already_seen_not_readded(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.email_scanner import OutlookScanner, GRAPH_BASE_URL
    scanner = OutlookScanner()
    child_url = f"{GRAPH_BASE_URL}/me/mailFolders/f-root/childFolders?$top=100"
    parent = {"id": "f-root", "displayName": "Root", "childFolderCount": 1}
    child = {"id": "f-child", "displayName": "Child", "childFolderCount": 0}
    calls: list[str] = []

    class FakeClient:
        async def get(self, url, headers=None):
            del headers
            calls.append(url)
            if "f-root/childFolders" in url:
                return type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: {"value": [child], "@odata.nextLink": None}})()
            return type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: {"value": [parent], "@odata.nextLink": child_url}})()

    folders = [f async for f in scanner._iter_mail_folders(FakeClient(), {})]
    assert {f["id"] for f in folders} == {"f-root", "f-child"}
    assert calls.count(child_url) == 1


def test_build_imap_criteria_all_modes() -> None:
    from app.services.email_scanner import _build_imap_criteria, ScanOptions

    assert _build_imap_criteria(None) == "ALL"
    assert _build_imap_criteria(ScanOptions()) == "ALL"

    result = _build_imap_criteria(ScanOptions(unread_only=True))
    assert result != "ALL"

    since_dt = datetime(2024, 6, 15, 10, 30, tzinfo=timezone.utc)
    result_since = _build_imap_criteria(ScanOptions(since=since_dt))
    assert result_since != "ALL"

    naive_since = datetime(2024, 6, 15, 10, 30)
    result_naive = _build_imap_criteria(ScanOptions(since=naive_since))
    assert result_naive != "ALL"

    combined = _build_imap_criteria(ScanOptions(unread_only=True, since=since_dt))
    assert combined != "ALL"


def test_imap_scan_options_unread_only(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    from app.services.email_scanner import ImapScanner, ScanOptions
    from app.services import email_scanner

    captured: dict[str, object] = {}

    class FakeMailbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self.folder = _FakeFolderManager()

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                return _FakeContextManager(self)

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            captured["criteria"] = criteria
            return []

    monkeypatch.setattr(email_scanner, "MailBox", FakeMailbox)
    account = EmailAccount(
        id=1, name="imap", type="imap", host="imap.example.com", port=993,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    options = ScanOptions(unread_only=True)
    ImapScanner()._scan_sync(account, None, options)
    assert captured["criteria"] != "ALL"


def test_imap_scan_options_since_client_side_filter(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    from app.services.email_scanner import ImapScanner, ScanOptions
    from app.services import email_scanner

    old_msg = SimpleNamespace(uid="1", subject="old", text="", html="", from_="a@test", date="2023-01-01T00:00:00Z", attachments=[], headers={})
    new_msg = SimpleNamespace(uid="2", subject="new", text="", html="", from_="b@test", date="2025-01-01T00:00:00Z", attachments=[], headers={})

    class FakeMailbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self.folder = _FakeFolderManager()

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                return _FakeContextManager(self)

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            return [old_msg, new_msg]

    monkeypatch.setattr(email_scanner, "MailBox", FakeMailbox)
    account = EmailAccount(
        id=1, name="imap", type="imap", host="imap.example.com", port=993,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    since = datetime(2024, 6, 1, tzinfo=timezone.utc)
    emails = ImapScanner()._scan_sync(account, None, ScanOptions(since=since))
    assert {e.uid for e in emails} == {"2"}


def test_imap_scan_options_reset_state_discards_existing_state(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    from app.services.email_scanner import ImapScanner, ScanOptions, _serialize_imap_state
    from app.services import email_scanner

    msg = SimpleNamespace(uid="5", subject="s", text="", html="", from_="a@test", date="2024-01-01T00:00:00Z", attachments=[], headers={})

    class FakeMailbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self.folder = _FakeFolderManager()

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                return _FakeContextManager(self)

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            return [msg]

    monkeypatch.setattr(email_scanner, "MailBox", FakeMailbox)
    account = EmailAccount(
        id=1, name="imap", type="imap", host="imap.example.com", port=993,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    prior_state = _serialize_imap_state({"INBOX": {"uid": "999", "uidvalidity": "12345"}})
    emails = ImapScanner()._scan_sync(account, prior_state, ScanOptions(reset_state=True))
    assert len(emails) == 1
    assert emails[0].uid == "5"


@pytest.mark.asyncio
async def test_outlook_scan_options_unread_only_adds_filter(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from app.services.email_scanner import OutlookScanner, ScanOptions
    scanner = OutlookScanner()
    account = EmailAccount(id=50, name="o", type="outlook", username="c-id", oauth_token_path=str(tmp_path / "c.json"))

    folder = {"id": "fi", "displayName": "Inbox", "wellKnownName": "inbox", "totalItemCount": 1, "childFolderCount": 0}
    captured_params: list = []

    class FakeResp:
        def __init__(self, d): self._d = d
        def raise_for_status(self): return None
        def json(self): return self._d

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None, params=None):
            del headers
            if "/mailFolders" in url and "/messages" not in url:
                return FakeResp({"value": [folder], "@odata.nextLink": None})
            captured_params.append(params)
            return FakeResp({"value": [], "@odata.nextLink": None})

    monkeypatch.setattr(email_scanner.httpx, "AsyncClient", lambda timeout: FakeClient())
    monkeypatch.setattr(scanner, "_acquire_access_token", AsyncMock(return_value="tok"))
    await scanner.scan(account, last_uid=None, options=ScanOptions(unread_only=True))
    assert captured_params and captured_params[0] is not None
    assert "isRead eq false" in captured_params[0].get("$filter", "")


@pytest.mark.asyncio
async def test_outlook_scan_options_since_adds_filter(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from app.services.email_scanner import OutlookScanner, ScanOptions
    scanner = OutlookScanner()
    account = EmailAccount(id=51, name="o", type="outlook", username="c-id", oauth_token_path=str(tmp_path / "c.json"))

    folder = {"id": "fi", "displayName": "Inbox", "wellKnownName": "inbox", "totalItemCount": 1, "childFolderCount": 0}
    captured_params: list = []

    class FakeResp:
        def __init__(self, d): self._d = d
        def raise_for_status(self): return None
        def json(self): return self._d

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None, params=None):
            del headers
            if "/mailFolders" in url and "/messages" not in url:
                return FakeResp({"value": [folder], "@odata.nextLink": None})
            captured_params.append(params)
            return FakeResp({"value": [], "@odata.nextLink": None})

    monkeypatch.setattr(email_scanner.httpx, "AsyncClient", lambda timeout: FakeClient())
    monkeypatch.setattr(scanner, "_acquire_access_token", AsyncMock(return_value="tok"))
    since = datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc)
    await scanner.scan(account, last_uid=None, options=ScanOptions(since=since))
    assert captured_params and captured_params[0] is not None
    flt = captured_params[0].get("$filter", "")
    assert "receivedDateTime ge 2024-06-15T12:00:00Z" in flt


@pytest.mark.asyncio
async def test_outlook_scan_options_combined_with_incremental_state(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from app.services.email_scanner import OutlookScanner, ScanOptions
    import json as _json
    scanner = OutlookScanner()
    account = EmailAccount(id=52, name="o", type="outlook", username="c-id", oauth_token_path=str(tmp_path / "c.json"))

    folder = {"id": "fi", "displayName": "Inbox", "wellKnownName": "inbox", "totalItemCount": 1, "childFolderCount": 0}
    captured_params: list = []

    class FakeResp:
        def __init__(self, d): self._d = d
        def raise_for_status(self): return None
        def json(self): return self._d

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None, params=None):
            del headers
            if "/mailFolders" in url and "/messages" not in url:
                return FakeResp({"value": [folder], "@odata.nextLink": None})
            captured_params.append(params)
            return FakeResp({"value": [], "@odata.nextLink": None})

    monkeypatch.setattr(email_scanner.httpx, "AsyncClient", lambda timeout: FakeClient())
    monkeypatch.setattr(scanner, "_acquire_access_token", AsyncMock(return_value="tok"))
    prior_state = _json.dumps({"fi": "2024-01-01T00:00:00Z"})
    since = datetime(2024, 6, 1, tzinfo=timezone.utc)
    await scanner.scan(account, last_uid=prior_state, options=ScanOptions(unread_only=True, since=since))
    assert captured_params and captured_params[0] is not None
    flt = captured_params[0].get("$filter", "")
    assert "receivedDateTime ge 2024-01-01T00:00:00Z" in flt
    assert "isRead eq false" in flt
    assert "receivedDateTime ge 2024-06-01" in flt


@pytest.mark.asyncio
async def test_outlook_scan_options_reset_state(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from app.services.email_scanner import OutlookScanner, ScanOptions
    import json as _json
    scanner = OutlookScanner()
    account = EmailAccount(id=53, name="o", type="outlook", username="c-id", oauth_token_path=str(tmp_path / "c.json"))

    folder = {"id": "fi", "displayName": "Inbox", "wellKnownName": "inbox", "totalItemCount": 1, "childFolderCount": 0}
    captured_params: list = []

    class FakeResp:
        def __init__(self, d): self._d = d
        def raise_for_status(self): return None
        def json(self): return self._d

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None, params=None):
            del headers
            if "/mailFolders" in url and "/messages" not in url:
                return FakeResp({"value": [folder], "@odata.nextLink": None})
            captured_params.append(params)
            return FakeResp({"value": [], "@odata.nextLink": None})

    monkeypatch.setattr(email_scanner.httpx, "AsyncClient", lambda timeout: FakeClient())
    monkeypatch.setattr(scanner, "_acquire_access_token", AsyncMock(return_value="tok"))
    prior_state = _json.dumps({"fi": "2024-01-01T00:00:00Z"})
    await scanner.scan(account, last_uid=prior_state, options=ScanOptions(reset_state=True))
    assert captured_params and captured_params[0] is not None
    flt = captured_params[0].get("$filter", "")
    assert "receivedDateTime gt" not in flt


def test_pop3_scan_options_since_filters_old_messages(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    from app.services.email_scanner import Pop3Scanner, ScanOptions
    from app.services import email_scanner
    import poplib as real_poplib

    scanner = Pop3Scanner()
    account = EmailAccount(
        id=60, name="pop3", type="pop3", host="pop.example.com", port=995,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )

    old_raw = b"From: a@test\r\nDate: Mon, 01 Jan 2023 00:00:00 +0000\r\nSubject: old\r\nMessage-ID: <old@test>\r\n\r\nbody"
    new_raw = b"From: b@test\r\nDate: Mon, 01 Jan 2025 00:00:00 +0000\r\nSubject: new\r\nMessage-ID: <new@test>\r\n\r\nbody"

    class FakePop3:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port

        def user(self, u): del u
        def pass_(self, p): del p
        def list(self):
            return (b"+OK", [b"1 123", b"2 234"], 0)
        def retr(self, idx):
            if idx == 1:
                return (b"+OK", old_raw.split(b"\r\n"), 0)
            return (b"+OK", new_raw.split(b"\r\n"), 0)
        def quit(self): pass
        def close(self): pass

    monkeypatch.setattr(email_scanner.poplib, "POP3_SSL", FakePop3)
    since = datetime(2024, 6, 1, tzinfo=timezone.utc)
    emails = scanner._scan_sync(account, None, ScanOptions(since=since))
    assert [e.subject for e in emails] == ["new"]


def test_pop3_scan_options_unread_only_is_noop(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    from app.services.email_scanner import Pop3Scanner, ScanOptions
    from app.services import email_scanner

    scanner = Pop3Scanner()
    account = EmailAccount(
        id=61, name="pop3", type="pop3", host="pop.example.com", port=995,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )

    raw = b"From: a@test\r\nDate: Mon, 01 Jan 2025 00:00:00 +0000\r\nSubject: msg\r\nMessage-ID: <msg@test>\r\n\r\nbody"

    class FakePop3:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port

        def user(self, u): del u
        def pass_(self, p): del p
        def list(self):
            return (b"+OK", [b"1 100"], 0)
        def retr(self, idx):
            del idx
            return (b"+OK", raw.split(b"\r\n"), 0)
        def quit(self): pass
        def close(self): pass

    monkeypatch.setattr(email_scanner.poplib, "POP3_SSL", FakePop3)
    emails_no_opt = scanner._scan_sync(account, None, None)
    emails_unread = scanner._scan_sync(account, None, ScanOptions(unread_only=True))
    assert len(emails_no_opt) == len(emails_unread) == 1


def test_pop3_scan_options_reset_state_ignores_previous_uid(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    from app.services.email_scanner import Pop3Scanner, ScanOptions
    from app.services import email_scanner
    import json as _json

    scanner = Pop3Scanner()
    account = EmailAccount(
        id=62, name="pop3", type="pop3", host="pop.example.com", port=995,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    raw = b"From: a@test\r\nDate: Mon, 01 Jan 2025 00:00:00 +0000\r\nSubject: msg\r\nMessage-ID: <m@test>\r\n\r\nbody"

    class FakePop3:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port

        def user(self, u): del u
        def pass_(self, p): del p
        def list(self):
            return (b"+OK", [b"1 100"], 0)
        def retr(self, idx):
            del idx
            return (b"+OK", raw.split(b"\r\n"), 0)
        def quit(self): pass
        def close(self): pass

    monkeypatch.setattr(email_scanner.poplib, "POP3_SSL", FakePop3)
    prior_uids = _json.dumps(["m@test"])
    emails = scanner._scan_sync(account, prior_uids, ScanOptions(reset_state=True))
    assert len(emails) == 1


def test_imap_scan_mailbox_fetch_error_mid_iteration_is_caught(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    """Transient MailboxFetchError raised while iterating the fetch generator
    (e.g. QQ Mail returning 'NO Data: System busy!') must NOT kill the whole scan.
    The folder is abandoned with its current highest_uid preserved, and scanning
    continues to the next folder."""
    from app.services.email_scanner import ImapScanner
    from app.services import email_scanner
    from imap_tools.errors import MailboxFetchError

    inbox_msg_1 = SimpleNamespace(uid="10", subject="ok", text="", html="", from_="a@test", date="2024-01-01T00:00:00Z", attachments=[], headers={})
    inbox_msg_2 = SimpleNamespace(uid="11", subject="ok2", text="", html="", from_="b@test", date="2024-01-02T00:00:00Z", attachments=[], headers={})
    archive_msg = SimpleNamespace(uid="5", subject="archive-ok", text="", html="", from_="c@test", date="2024-02-01T00:00:00Z", attachments=[], headers={})

    def failing_iter():
        yield inbox_msg_1
        yield inbox_msg_2
        raise MailboxFetchError(command_result=(b"NO", [b"System busy!"]), expected=b"OK")

    class FlakyFetchMailbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self._folder = _FakeFolderManager(folders=[
                    _FakeFolderInfo("INBOX"),
                    _FakeFolderInfo("Archive"),
                ])

        @property
        def folder(self):
            return self._folder

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                return _FakeContextManager(self)

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            if self._folder.current == "INBOX":
                return failing_iter()
            return [archive_msg]

    monkeypatch.setattr(email_scanner, "MailBox", FlakyFetchMailbox)
    account = EmailAccount(
            id=1, name="imap-generic", type="imap", host="imap.example.com", port=993,
            username="user@example.com",
            password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
        )
    scanner = ImapScanner()
    emails = scanner._scan_sync(account, None)

    uids = {e.uid for e in emails}
    assert "10" in uids, "messages yielded before the error must survive"
    assert "11" in uids, "messages yielded before the error must survive"
    assert "5" in uids, "next folder must still be scanned after one folder's transient error"



def test_set_imap_keepalive_covers_all_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    """_set_imap_keepalive should be a safe no-op on None or broken clients,
    and should exercise both Linux and macOS socket option paths."""
    from app.services.email_scanner import _set_imap_keepalive
    from app.services import email_scanner

    _set_imap_keepalive(None)

    class BrokenClient:
        def socket(self):
            raise RuntimeError("no socket")

    _set_imap_keepalive(BrokenClient())

    class FakeSocket:
        def __init__(self):
            self.options_set: list = []
            self.read_timeout: float | None = None

        def settimeout(self, timeout):
            self.read_timeout = timeout

        def setsockopt(self, level, name, value):
            self.options_set.append((level, name, value))

    class GoodClient:
        def __init__(self):
            self.sock = FakeSocket()

        def socket(self):
            return self.sock

    client = GoodClient()
    _set_imap_keepalive(client)
    assert len(client.sock.options_set) >= 1
    assert client.sock.read_timeout == email_scanner.IMAP_READ_TIMEOUT, (
        "read timeout must be applied alongside keepalive; this prevents the QQ 'System busy' / SSL half-open hang"
    )

    class BrokenSetOpt:
        def socket(self):
            class S:
                def setsockopt(self, *a, **kw):
                    raise OSError("bad option")
            return S()

    _set_imap_keepalive(BrokenSetOpt())


def test_imap_scan_skips_unchanged_folder_via_status_preflight(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    """When UIDNEXT and MESSAGES match the stored state, the folder should be
    skipped entirely — no fetch, no emails returned for that folder."""
    from app.services.email_scanner import ImapScanner, _serialize_imap_state
    from app.services import email_scanner

    fetch_calls: list = []

    class UnchangedMailbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self._folder = _FakeFolderManager(
                    folders=[_FakeFolderInfo("INBOX")],
                    uidvalidity_map={"INBOX": 42},
                    uidnext_map={"INBOX": 101},
                    messages_map={"INBOX": 50},
                )

        @property
        def folder(self):
            return self._folder

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                return _FakeContextManager(self)

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            fetch_calls.append(criteria)
            return []

    monkeypatch.setattr(email_scanner, "MailBox", UnchangedMailbox)
    account = EmailAccount(
        id=1, name="imap", type="imap", host="imap.example.com", port=993,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )

    prior = _serialize_imap_state({
        "INBOX": {"uid": "100", "uidvalidity": "42", "uidnext": "101", "messages": "50"}
    })

    emails = ImapScanner()._scan_sync(account, prior)
    assert emails == []
    assert fetch_calls == []


def test_imap_scan_publishes_progress_callbacks(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    """The scanner should invoke progress_callback at folder boundaries with
    folder telemetry fields (total_folders, current_folder_idx, current_folder_name)."""
    from app.services.email_scanner import ImapScanner
    from app.services import email_scanner

    msg = SimpleNamespace(uid="1", subject="s", text="", html="", from_="a@test", date="2024-01-01T00:00:00Z", attachments=[], headers={})

    class SimpleMailbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self._folder = _FakeFolderManager()

        @property
        def folder(self):
            return self._folder

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                return _FakeContextManager(self)

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            return [msg]

    monkeypatch.setattr(email_scanner, "MailBox", SimpleMailbox)
    captured: list = []

    def cb(update):
        captured.append(update)

    account = EmailAccount(
        id=1, name="imap", type="imap", host="imap.example.com", port=993,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    ImapScanner()._scan_sync(account, None, None, cb)

    assert any("total_folders" in u for u in captured)
    assert any("current_folder_name" in u for u in captured)
    assert any("folder_fetch_msg" in u for u in captured)


def test_imap_scan_progress_callback_exceptions_are_swallowed(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    """A buggy progress_callback must not disrupt the scan."""
    from app.services.email_scanner import ImapScanner
    from app.services import email_scanner

    msg = SimpleNamespace(uid="1", subject="s", text="", html="", from_="a@test", date="2024-01-01T00:00:00Z", attachments=[], headers={})

    class SimpleMailbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self._folder = _FakeFolderManager()

        @property
        def folder(self):
            return self._folder

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                return _FakeContextManager(self)

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            return [msg]

    monkeypatch.setattr(email_scanner, "MailBox", SimpleMailbox)

    def broken_cb(update):
        raise RuntimeError("callback failed")

    account = EmailAccount(
        id=1, name="imap", type="imap", host="imap.example.com", port=993,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    emails = ImapScanner()._scan_sync(account, None, None, broken_cb)
    assert len(emails) == 1


def test_imap_scan_outer_session_drop_preserves_partial_state(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    """If the outer MailBox.__exit__ or folder.list() raises an IMAP_CONNECTION_ERROR,
    we should log a warning but still return whatever emails we accumulated and
    serialize _last_scan_state from partial state_out."""
    from app.services.email_scanner import ImapScanner
    from app.services import email_scanner

    class DropAfterListMailbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self._folder = _FakeFolderManager(folders=[])

        @property
        def folder(self):
            return self._folder

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                return self

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            raise ssl.SSLEOFError("session dropped during exit")

    monkeypatch.setattr(email_scanner, "MailBox", DropAfterListMailbox)
    account = EmailAccount(
        id=1, name="imap", type="imap", host="imap.example.com", port=993,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    scanner = ImapScanner()
    emails = scanner._scan_sync(account, None)
    assert emails == []
    assert scanner._last_scan_state == "{}"


def test_imap_scan_many_folder_emails_triggers_interim_progress(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    """Every 200 emails during a folder scan, the progress_callback should be called
    with a running total_emails and folder_fetch_msg update."""
    from app.services.email_scanner import ImapScanner
    from app.services import email_scanner

    many_msgs = [
        SimpleNamespace(
            uid=str(i),
            subject=f"s{i}",
            text="",
            html="",
            from_="a@test",
            date="2024-01-01T00:00:00Z",
            attachments=[],
            headers={},
        )
        for i in range(1, 251)
    ]

    class BigMailbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self._folder = _FakeFolderManager()

        @property
        def folder(self):
            return self._folder

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                return _FakeContextManager(self)

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            return many_msgs

    monkeypatch.setattr(email_scanner, "MailBox", BigMailbox)
    captured: list = []
    account = EmailAccount(
        id=1, name="imap", type="imap", host="imap.example.com", port=993,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    emails = ImapScanner()._scan_sync(account, None, None, captured.append)
    assert len(emails) == 250
    interim_updates = [u for u in captured if "msgs" in u.get("folder_fetch_msg", "") and "total_emails" in u]
    assert len(interim_updates) >= 1


def test_imap_scan_parallel_fetch_when_uids_exceed_threshold(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    """When IMAP_FETCH_WORKERS > 1 and folder has >= IMAP_PARALLEL_THRESHOLD UIDs,
    parallel workers are used and results are merged."""
    from app.services import email_scanner as es
    from app.services.email_scanner import ImapScanner

    monkeypatch.setattr(es, "IMAP_FETCH_WORKERS", 2)
    monkeypatch.setattr(es, "IMAP_PARALLEL_THRESHOLD", 3)

    all_msgs = [
        SimpleNamespace(uid=str(i), subject=f"s{i}", text="", html="", from_=f"u{i}@test",
                        date="2024-01-01T00:00:00Z", attachments=[], headers={"Message-ID": f"<m{i}@test>"})
        for i in range(1, 6)
    ]
    uid_list_returned = [str(i) for i in range(1, 6)]

    class ParallelMailbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self._folder = _FakeFolderManager()

        @property
        def folder(self):
            return self._folder

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                return _FakeContextManager(self)

        def uids(self, criteria):
            del criteria
            return uid_list_returned

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            return iter(all_msgs)

    monkeypatch.setattr(es, "MailBox", ParallelMailbox)
    account = EmailAccount(
            id=1, name="imap-generic", type="imap", host="imap.example.com", port=993,
            username="user@example.com",
            password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
        )
    scanner = ImapScanner()
    emails = scanner._scan_sync(account, None)

    assert len(emails) == 5, f"expected 5 emails from parallel fetch; got {len(emails)}"


def test_imap_scan_parallel_worker_error_falls_back_to_single_conn(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    """If a parallel worker errors out, the scan falls back to single-connection for that folder."""
    from app.services import email_scanner as es
    from app.services.email_scanner import ImapScanner, _fetch_folder_worker

    monkeypatch.setattr(es, "IMAP_FETCH_WORKERS", 2)
    monkeypatch.setattr(es, "IMAP_PARALLEL_THRESHOLD", 3)

    all_msgs = [
        SimpleNamespace(uid=str(i), subject=f"s{i}", text="", html="", from_=f"u{i}@test",
                        date="2024-01-01T00:00:00Z", attachments=[], headers={"Message-ID": f"<m{i}@test>"})
        for i in range(1, 6)
    ]

    class FallbackMailbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self._folder = _FakeFolderManager()

        @property
        def folder(self):
            return self._folder

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                return _FakeContextManager(self)

        def uids(self, criteria):
            del criteria
            return ["1", "2", "3", "4", "5"]

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            return iter(all_msgs)

    monkeypatch.setattr(es, "MailBox", FallbackMailbox)

    def broken_worker(*args, **kwargs):
        del args, kwargs
        return [], "connection failed"

    monkeypatch.setattr(es, "_fetch_folder_worker", broken_worker)

    account = EmailAccount(
            id=1, name="imap-generic", type="imap", host="imap.example.com", port=993,
            username="user@example.com",
            password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
        )
    scanner = ImapScanner()
    emails = scanner._scan_sync(account, None)
    assert len(emails) == 5, f"after fallback from broken workers, single-conn should still yield all emails; got {len(emails)}"


def test_fetch_folder_worker_returns_empty_and_error_on_connection_failure(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    """_fetch_folder_worker catches IMAP_CONNECTION_ERRORS and returns empty list + error string."""
    from app.services import email_scanner as es

    class BrokenMailbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                raise OSError("connection refused")

    monkeypatch.setattr(es, "MailBox", BrokenMailbox)
    results, error = es._fetch_folder_worker(
        "imap.qq.com", 993, "user@qq.com", "secret", "INBOX", ["1", "2"], "ALL", None, None
    )
    assert results == []
    assert "connection refused" in error


def test_fetch_folder_worker_returns_messages_in_uid_list(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    """_fetch_folder_worker only returns messages whose UIDs are in the provided list."""
    from app.services import email_scanner as es

    all_msgs = [
        SimpleNamespace(uid="10", subject="s10", text="", html="", from_="a@test",
                        date="2024-01-01T00:00:00Z", attachments=[], headers={"Message-ID": "<m10@test>"}),
        SimpleNamespace(uid="20", subject="s20", text="", html="", from_="b@test",
                        date="2024-02-01T00:00:00Z", attachments=[], headers={"Message-ID": "<m20@test>"}),
        SimpleNamespace(uid="30", subject="s30", text="", html="", from_="c@test",
                        date="2024-03-01T00:00:00Z", attachments=[], headers={}),
    ]

    class WorkerMailbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self._folder = _FakeFolderManager()

        @property
        def folder(self):
            return self._folder

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                return _FakeContextManager(self)

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            return iter(all_msgs)

    monkeypatch.setattr(es, "MailBox", WorkerMailbox)
    results, error = es._fetch_folder_worker(
        "imap.qq.com", 993, "user@qq.com", "secret", "INBOX", ["10", "30"], "ALL", None, None
    )
    assert error == ""
    assert {r["uid"] for r in results} == {"10", "30"}


def test_imap_scan_falls_back_to_single_conn_when_uids_below_threshold(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    """When UID count is below IMAP_PARALLEL_THRESHOLD, single-connection path is used."""
    from app.services import email_scanner as es
    from app.services.email_scanner import ImapScanner

    monkeypatch.setattr(es, "IMAP_FETCH_WORKERS", 4)
    monkeypatch.setattr(es, "IMAP_PARALLEL_THRESHOLD", 1000)

    worker_called = []

    def spy_worker(*args, **kwargs):
        worker_called.append(True)
        return [], ""

    monkeypatch.setattr(es, "_fetch_folder_worker", spy_worker)

    all_msgs = [
        SimpleNamespace(uid=str(i), subject=f"s{i}", text="", html="", from_=f"u{i}@test",
                        date="2024-01-01T00:00:00Z", attachments=[], headers={"Message-ID": f"<m{i}@test>"})
        for i in range(1, 6)
    ]

    class SmallMailbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self._folder = _FakeFolderManager()

        @property
        def folder(self):
            return self._folder

        def login(self, username, password, **kwargs):
                del kwargs
                del username, password
                return _FakeContextManager(self)

        def uids(self, criteria):
            del criteria
            return ["1", "2", "3", "4", "5"]

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            return iter(all_msgs)

    monkeypatch.setattr(es, "MailBox", SmallMailbox)
    account = EmailAccount(
        id=1, name="imap", type="imap", host="imap.example.com", port=993,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    emails = ImapScanner()._scan_sync(account, None)
    assert worker_called == [], "worker must NOT be called when UIDs < threshold"
    assert len(emails) == 5


def test_fetch_folder_worker_applies_since_filter(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    """since filter is applied client-side inside _fetch_folder_worker."""
    from app.services import email_scanner as es
    from app.services.email_scanner import ScanOptions

    old_msg = SimpleNamespace(uid="1", subject="old", text="", html="", from_="a@test",
                              date="2023-06-01T00:00:00Z", attachments=[], headers={"Message-ID": "<old@t>"})
    new_msg = SimpleNamespace(uid="2", subject="new", text="", html="", from_="b@test",
                              date="2025-01-01T00:00:00Z", attachments=[], headers={"Message-ID": "<new@t>"})

    class WorkerMBX:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self._folder = _FakeFolderManager()

        @property
        def folder(self):
            return self._folder

        def login(self, u, p, **kwargs):
                del kwargs
                del u, p
                return _FakeContextManager(self)

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            return [old_msg, new_msg]

    monkeypatch.setattr(es, "MailBox", WorkerMBX)
    since = datetime(2024, 1, 1, tzinfo=timezone.utc)
    opts = ScanOptions(since=since)
    results, error = es._fetch_folder_worker(
        "h", 993, "u", "p", "INBOX", ["1", "2"], "ALL", since, opts
    )
    assert error == ""
    assert [r["uid"] for r in results] == ["2"]


def test_imap_scan_parallel_future_exception_falls_back(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    """If a worker future raises (not just returns error string), parallel falls back."""
    from app.services import email_scanner as es
    from app.services.email_scanner import ImapScanner

    monkeypatch.setattr(es, "IMAP_FETCH_WORKERS", 2)
    monkeypatch.setattr(es, "IMAP_PARALLEL_THRESHOLD", 3)

    all_msgs = [
        SimpleNamespace(uid=str(i), subject=f"s{i}", text="", html="", from_=f"u{i}@t",
                        date="2024-01-01T00:00:00Z", attachments=[], headers={"Message-ID": f"<m{i}@t>"})
        for i in range(1, 6)
    ]

    class ExcMailbox:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self._folder = _FakeFolderManager()

        @property
        def folder(self):
            return self._folder

        def login(self, u, p, **kwargs):
                del kwargs
                del u, p
                return _FakeContextManager(self)

        def uids(self, criteria):
            del criteria
            return ["1", "2", "3", "4", "5"]

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            return iter(all_msgs)

    def raising_worker(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("unexpected worker crash")

    monkeypatch.setattr(es, "MailBox", ExcMailbox)
    monkeypatch.setattr(es, "_fetch_folder_worker", raising_worker)

    account = EmailAccount(
        id=1, name="imap", type="imap", host="h", port=993,
        username="u@t",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    emails = ImapScanner()._scan_sync(account, None)
    assert len(emails) == 5, f"fallback from raising worker must still yield emails; got {len(emails)}"


def test_imap_scan_parallel_interim_progress_published(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    """Progress callback receives interim updates during parallel fetch for large batches."""
    from app.services import email_scanner as es
    from app.services.email_scanner import ImapScanner

    monkeypatch.setattr(es, "IMAP_FETCH_WORKERS", 2)
    monkeypatch.setattr(es, "IMAP_PARALLEL_THRESHOLD", 3)

    all_msgs = [
        SimpleNamespace(uid=str(i), subject=f"s{i}", text="", html="", from_=f"u{i}@t",
                        date="2024-01-01T00:00:00Z", attachments=[], headers={"Message-ID": f"<m{i}@t>"})
        for i in range(1, 222)
    ]
    uid_list = [str(i) for i in range(1, 222)]

    class BigParallelMBX:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self._folder = _FakeFolderManager()

        @property
        def folder(self):
            return self._folder

        def login(self, u, p, **kwargs):
                del kwargs
                del u, p
                return _FakeContextManager(self)

        def uids(self, criteria):
            del criteria
            return uid_list

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            return iter(all_msgs)

    monkeypatch.setattr(es, "MailBox", BigParallelMBX)
    captured: list = []
    account = EmailAccount(
        id=1, name="imap", type="imap", host="h", port=993,
        username="u@t",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    emails = ImapScanner()._scan_sync(account, None, None, captured.append)
    assert len(emails) == 221
    parallel_updates = [u for u in captured if "parallel" in u.get("folder_fetch_msg", "")]
    assert len(parallel_updates) >= 1, "should publish interim progress during parallel fetch"


def test_imap_scan_parallel_with_limit_applies_uid_cap(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    """When FIRST_SCAN_LIMIT is set, the parallel path respects it via uid list slicing."""
    from app.services import email_scanner as es
    from app.services.email_scanner import ImapScanner

    monkeypatch.setattr(es, "IMAP_FETCH_WORKERS", 2)
    monkeypatch.setattr(es, "IMAP_PARALLEL_THRESHOLD", 3)
    monkeypatch.setattr(es, "FIRST_SCAN_LIMIT", 4)

    all_msgs = [
        SimpleNamespace(uid=str(i), subject=f"s{i}", text="", html="", from_=f"u{i}@t",
                        date="2024-01-01T00:00:00Z", attachments=[], headers={"Message-ID": f"<m{i}@t>"})
        for i in range(1, 9)
    ]
    uid_list = [str(i) for i in range(1, 9)]

    class LimitedMBX:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self._folder = _FakeFolderManager()

        @property
        def folder(self):
            return self._folder

        def login(self, u, p, **kwargs):
                del kwargs
                del u, p
                return _FakeContextManager(self)

        def uids(self, criteria):
            del criteria
            return uid_list

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, reverse, mark_seen, headers_only, bulk
            msgs = all_msgs[:limit] if limit else all_msgs
            return iter(msgs)

    monkeypatch.setattr(es, "MailBox", LimitedMBX)
    account = EmailAccount(
        id=1, name="imap", type="imap", host="h", port=993,
        username="u@t",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    emails = ImapScanner()._scan_sync(account, None)
    assert len(emails) <= 4, f"FIRST_SCAN_LIMIT=4 should cap results; got {len(emails)}"


def test_imap_scan_workers_one_uses_single_conn_directly(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    """IMAP_FETCH_WORKERS=1 disables parallel entirely; the n_workers<=1 branch is taken."""
    from app.services import email_scanner as es
    from app.services.email_scanner import ImapScanner

    monkeypatch.setattr(es, "IMAP_FETCH_WORKERS", 1)

    msg = SimpleNamespace(uid="1", subject="s", text="", html="", from_="a@test",
                          date="2024-01-01T00:00:00Z", attachments=[], headers={})

    class SingleWorkerMBX:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self._folder = _FakeFolderManager()

        @property
        def folder(self):
            return self._folder

        def login(self, u, p, **kwargs):
                del kwargs
                del u, p
                return _FakeContextManager(self)

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            return [msg]

    monkeypatch.setattr(es, "MailBox", SingleWorkerMBX)
    account = EmailAccount(
        id=1, name="imap", type="imap", host="h", port=993,
        username="u@t",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    emails = ImapScanner()._scan_sync(account, None)
    assert len(emails) == 1


def test_imap_scan_parallel_handles_no_message_id_in_msg_dict(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    """Messages with empty Message-ID in parallel path are added without dedup tracking."""
    from app.services import email_scanner as es
    from app.services.email_scanner import ImapScanner

    monkeypatch.setattr(es, "IMAP_FETCH_WORKERS", 2)
    monkeypatch.setattr(es, "IMAP_PARALLEL_THRESHOLD", 2)

    no_id_msgs = [
        SimpleNamespace(uid=str(i), subject=f"s{i}", text="", html="", from_=f"u{i}@t",
                        date="2024-01-01T00:00:00Z", attachments=[], headers={})
        for i in range(1, 4)
    ]
    uid_list = ["1", "2", "3"]

    class NoIdMBX:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self._folder = _FakeFolderManager()

        @property
        def folder(self):
            return self._folder

        def login(self, u, p, **kwargs):
                del kwargs
                del u, p
                return _FakeContextManager(self)

        def uids(self, criteria):
            del criteria
            return uid_list

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            return iter(no_id_msgs)

    monkeypatch.setattr(es, "MailBox", NoIdMBX)
    account = EmailAccount(
        id=1, name="imap", type="imap", host="h", port=993,
        username="u@t",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    emails = ImapScanner()._scan_sync(account, None)
    assert len(emails) == 3


def test_imap_scan_parallel_dedup_across_workers(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    """Cross-folder dedup works correctly in the parallel path: same Message-ID seen
    by multiple workers is only included once, and highest_uid is updated correctly."""
    from app.services import email_scanner as es
    from app.services.email_scanner import ImapScanner

    monkeypatch.setattr(es, "IMAP_FETCH_WORKERS", 2)
    monkeypatch.setattr(es, "IMAP_PARALLEL_THRESHOLD", 3)

    shared_id = "<shared@test.com>"
    all_msgs = [
        SimpleNamespace(uid="10", subject="unique1", text="", html="", from_="a@t",
                        date="2024-01-01T00:00:00Z", attachments=[], headers={"Message-ID": shared_id}),
        SimpleNamespace(uid="20", subject="unique2", text="", html="", from_="b@t",
                        date="2024-02-01T00:00:00Z", attachments=[], headers={"Message-ID": shared_id}),
        SimpleNamespace(uid="30", subject="unique3", text="", html="", from_="c@t",
                        date="2024-03-01T00:00:00Z", attachments=[], headers={"Message-ID": "<other@test.com>"}),
    ]
    uid_list = ["10", "20", "30"]

    class DedupMBX:
        def __init__(self, host, port, **kwargs):
                del kwargs
                del host, port
                self._folder = _FakeFolderManager()

        @property
        def folder(self):
            return self._folder

        def login(self, u, p, **kwargs):
                del kwargs
                del u, p
                return _FakeContextManager(self)

        def uids(self, criteria):
            del criteria
            return uid_list

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            return iter(all_msgs)

    monkeypatch.setattr(es, "MailBox", DedupMBX)
    account = EmailAccount(
        id=1, name="imap", type="imap", host="h", port=993,
        username="u@t",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    emails = ImapScanner()._scan_sync(account, None)
    assert len(emails) == 2, f"dedup should collapse 2 msgs with same Message-ID to 1; got {len(emails)}"
    uids = {e.uid for e in emails}
    assert "30" in uids


def test_imap_scan_parallel_worker_timeout_falls_back_to_single_conn(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    """When a parallel worker future exceeds IMAP_PARALLEL_WORKER_TIMEOUT,
    the scan must catch the TimeoutError, retry once, and fall back to
    single-connection instead of letting the scan thread hang forever.

    This is the regression test for the v0.8.1 production hang on imap.qq.com
    where a single stuck worker blocked fut.result() indefinitely."""
    import concurrent.futures as cf
    from app.services import email_scanner as es
    from app.services.email_scanner import ImapScanner

    monkeypatch.setattr(es, "IMAP_FETCH_WORKERS", 2)
    monkeypatch.setattr(es, "IMAP_PARALLEL_THRESHOLD", 3)
    monkeypatch.setattr(es, "IMAP_PARALLEL_RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(es.time, "sleep", lambda _s: None)

    all_msgs = [
        SimpleNamespace(uid=str(i), subject=f"s{i}", text="", html="", from_=f"u{i}@t",
                        date="2024-01-01T00:00:00Z", attachments=[], headers={"Message-ID": f"<m{i}@t>"})
        for i in range(1, 6)
    ]

    class TimeoutMailbox:
        def __init__(self, host, port, **kwargs):
            del host, port, kwargs
            self._folder = _FakeFolderManager()

        @property
        def folder(self):
            return self._folder

        def login(self, u, p, **kwargs):
                del kwargs
                del u, p
                return _FakeContextManager(self)

        def uids(self, criteria):
            del criteria
            return ["1", "2", "3", "4", "5"]

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            return iter(all_msgs)

    class FakeTimeoutFuture:
        def result(self, timeout=None):
            del timeout
            raise cf.TimeoutError("simulated worker hang")

    class FakeTimeoutPool:
        def __init__(self, *a, **kw):
            del a, kw

        def submit(self, *a, **kw):
            del a, kw
            return FakeTimeoutFuture()

        def shutdown(self, *a, **kw):
            del a, kw

    monkeypatch.setattr(es, "MailBox", TimeoutMailbox)
    monkeypatch.setattr(es.concurrent.futures, "ThreadPoolExecutor", FakeTimeoutPool)

    account = EmailAccount(
            id=1, name="imap-generic", type="imap", host="imap.example.com", port=993,
            username="user@example.com",
            password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
        )
    scanner = ImapScanner()
    emails = scanner._scan_sync(account, None)
    assert len(emails) == 5, (
        "after worker-timeout path exhausts retries, single-connection fallback "
        "must still yield all 5 messages"
    )


def test_imap_scan_parallel_retry_preserves_longer_partial_on_second_failure(
    monkeypatch: pytest.MonkeyPatch, settings
) -> None:
    """When the parallel path fails TWICE but the second attempt yields MORE
    partial messages than the first (rare ordering), the scan must keep the
    larger partial set so we don't lose fail-resume progress."""
    from app.services import email_scanner as es
    from app.services.email_scanner import ImapScanner

    monkeypatch.setattr(es, "IMAP_FETCH_WORKERS", 2)
    monkeypatch.setattr(es, "IMAP_PARALLEL_THRESHOLD", 3)
    monkeypatch.setattr(es, "IMAP_PARALLEL_RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(es.time, "sleep", lambda _s: None)

    all_msgs = [
        SimpleNamespace(uid=str(i), subject=f"s{i}", text="", html="", from_=f"u{i}@t",
                        date="2024-01-01T00:00:00Z", attachments=[], headers={"Message-ID": f"<m{i}@t>"})
        for i in range(1, 6)
    ]
    partial_small = [
        {"uid": "1", "subject": "s1", "from_": "u1@t",
         "received_at": datetime(2024, 1, 1, tzinfo=timezone.utc), "headers": {"Message-ID": "<m1@t>"}},
    ]
    partial_big = [
        {"uid": "1", "subject": "s1", "from_": "u1@t",
         "received_at": datetime(2024, 1, 1, tzinfo=timezone.utc), "headers": {"Message-ID": "<m1@t>"}},
        {"uid": "2", "subject": "s2", "from_": "u2@t",
         "received_at": datetime(2024, 1, 1, tzinfo=timezone.utc), "headers": {"Message-ID": "<m2@t>"}},
        {"uid": "3", "subject": "s3", "from_": "u3@t",
         "received_at": datetime(2024, 1, 1, tzinfo=timezone.utc), "headers": {"Message-ID": "<m3@t>"}},
    ]
    call_count = {"n": 0}

    def worker_returns_varying_partial(*args, **kwargs):
        del args, kwargs
        call_count["n"] += 1
        if call_count["n"] <= 2:
            return list(partial_small), "transient: System busy"
        return list(partial_big), "transient: System busy"

    class MBX:
        def __init__(self, host, port, **kwargs):
            del host, port, kwargs
            self._folder = _FakeFolderManager()

        @property
        def folder(self):
            return self._folder

        def login(self, u, p, **kwargs):
                del kwargs
                del u, p
                return _FakeContextManager(self)

        def uids(self, criteria):
            del criteria
            return ["1", "2", "3", "4", "5"]

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            return iter(all_msgs)

    monkeypatch.setattr(es, "MailBox", MBX)
    monkeypatch.setattr(es, "_fetch_folder_worker", worker_returns_varying_partial)

    account = EmailAccount(
        id=1, name="imap-generic", type="imap", host="imap.example.com", port=993,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    scanner = ImapScanner()
    emails = scanner._scan_sync(account, None)
    uids = {e.uid for e in emails}
    assert "2" in uids and "3" in uids, (
        "partial messages from the larger second-attempt batch must be preserved and merged"
    )


def test_imap_scan_publishes_heartbeat_around_uids_search(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    """The server-side UID SEARCH on large Chinese mailboxes can take 30-120s.
    The scan must publish a heartbeat before and after the call so the UI
    doesn't show a frozen 'fetching ~N msgs' during the SEARCH round-trip."""
    from app.services import email_scanner as es
    from app.services.email_scanner import ImapScanner

    monkeypatch.setattr(es, "IMAP_FETCH_WORKERS", 2)
    monkeypatch.setattr(es, "IMAP_PARALLEL_THRESHOLD", 100)

    class MBX:
        def __init__(self, host, port, **kwargs):
            del host, port, kwargs
            self._folder = _FakeFolderManager()

        @property
        def folder(self):
            return self._folder

        def login(self, u, p, **kwargs):
                del kwargs
                del u, p
                return _FakeContextManager(self)

        def uids(self, criteria):
            del criteria
            return ["1", "2", "3"]

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            return iter([])

    monkeypatch.setattr(es, "MailBox", MBX)
    account = EmailAccount(
        id=1, name="generic-imap", type="imap", host="imap.example.com", port=993,
        username="user@example.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    captured: list[dict] = []
    ImapScanner()._scan_sync(account, None, progress_callback=captured.append)
    search_msgs = [u.get("folder_fetch_msg", "") for u in captured]
    assert any("searching UIDs" in m for m in search_msgs), (
        "must publish pre-search heartbeat so UI shows progress during slow SEARCH"
    )
    assert any("new UIDs to fetch" in m for m in search_msgs), (
        "must publish post-search heartbeat with UID count"
    )


def test_imap_scan_uids_failure_is_caught_and_falls_back(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    """If the server-side UID SEARCH raises (e.g. socket.timeout, QQ drops
    the connection), the scan must log a warning and fall back to the
    single-connection serial fetch path instead of crashing the account scan."""
    from app.services import email_scanner as es
    from app.services.email_scanner import ImapScanner
    import socket as _socket

    monkeypatch.setattr(es, "IMAP_FETCH_WORKERS", 2)
    monkeypatch.setattr(es, "IMAP_PARALLEL_THRESHOLD", 3)

    all_msgs = [
        SimpleNamespace(uid=str(i), subject=f"s{i}", text="", html="", from_=f"u{i}@t",
                        date="2024-01-01T00:00:00Z", attachments=[], headers={"Message-ID": f"<m{i}@t>"})
        for i in range(1, 4)
    ]

    class MBX:
        def __init__(self, host, port, **kwargs):
            del host, port, kwargs
            self._folder = _FakeFolderManager()

        @property
        def folder(self):
            return self._folder

        def login(self, u, p, **kwargs):
                del kwargs
                del u, p
                return _FakeContextManager(self)

        def uids(self, criteria):
            del criteria
            raise _socket.timeout("simulated SEARCH stall")

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            return iter(all_msgs)

    monkeypatch.setattr(es, "MailBox", MBX)
    account = EmailAccount(
            id=1, name="imap-generic", type="imap", host="imap.example.com", port=993,
            username="user@example.com",
            password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
        )
    emails = ImapScanner()._scan_sync(account, None)
    assert len(emails) == 3, (
        "after uids() times out, single-connection fallback must still yield all 3 messages "
        "(this is the mechanism that prevents v0.8.1's 44-min hang on imap.qq.com)"
    )


def test_is_qq_imap_matches_account_type_and_host_variants() -> None:
    """_is_qq_imap must recognize QQ via both account.type=='qq' and the
    imap.qq.com / imap.exmail.qq.com host (users may manually choose
    type='imap' in the form)."""
    from app.services.email_scanner import _is_qq_imap

    qq_by_type = EmailAccount(id=1, name="qq", type="qq", host="", username="a@qq.com")
    assert _is_qq_imap(qq_by_type) is True

    qq_by_host = EmailAccount(id=2, name="manual-qq", type="imap", host="imap.qq.com", username="b@qq.com")
    assert _is_qq_imap(qq_by_host) is True

    qq_exmail = EmailAccount(id=3, name="exmail", type="imap", host="imap.exmail.qq.com", username="c@company.cn")
    assert _is_qq_imap(qq_exmail) is True

    generic = EmailAccount(id=4, name="gmail", type="imap", host="imap.gmail.com", username="d@example.com")
    assert _is_qq_imap(generic) is False

    assert _is_qq_imap(None) is False
    assert _is_qq_imap(None, host="imap.qq.com") is True
    assert _is_qq_imap(None, host="imap.gmail.com") is False


def test_build_qq_fetch_criteria_uses_uid_range_when_baseline_exists() -> None:
    """QQ's SEARCH ALL on a 35k-msg INBOX times out server-side (~30-60s).
    When we have a saved highest-UID baseline, we must restrict SEARCH to
    UID last+1:* so QQ returns immediately. Also layer unread_only / since
    options on top if provided."""
    from app.services.email_scanner import _build_qq_fetch_criteria, ScanOptions

    crit_no_baseline = _build_qq_fetch_criteria("", None)
    assert str(crit_no_baseline) == "ALL"

    crit_with_baseline = _build_qq_fetch_criteria("100", None)
    rendered = str(crit_with_baseline)
    assert "101:*" in rendered or "UID 101" in rendered, rendered

    crit_unread = _build_qq_fetch_criteria("100", ScanOptions(unread_only=True))
    rendered_unread = str(crit_unread)
    assert "101:*" in rendered_unread or "UID 101" in rendered_unread, rendered_unread
    assert "UNSEEN" in rendered_unread or "NOT SEEN" in rendered_unread, rendered_unread

    from datetime import datetime as _dt, timezone as _tz
    since_opt = ScanOptions(since=_dt(2024, 1, 1, tzinfo=_tz.utc))
    crit_since = _build_qq_fetch_criteria("100", since_opt)
    rendered_since = str(crit_since)
    assert "101:*" in rendered_since or "UID 101" in rendered_since, rendered_since
    assert "SINCE" in rendered_since, rendered_since

    crit_nondigit = _build_qq_fetch_criteria("abc", None)
    assert str(crit_nondigit) == "ALL"


def test_qq_scan_forces_single_connection_regardless_of_uid_count(
    monkeypatch: pytest.MonkeyPatch, settings
) -> None:
    """Even with thousands of UIDs in INBOX, a QQ account MUST NOT use the
    parallel fetch path. QQ's per-account connection limit is ~1-2; 4
    parallel workers reliably trigger 'System busy!' + SSL corruption.
    This regression test ensures _fetch_folder_worker is never invoked for
    a QQ account, even when IMAP_FETCH_WORKERS/IMAP_PARALLEL_THRESHOLD
    globally would otherwise enable parallel."""
    from app.services import email_scanner as es
    from app.services.email_scanner import ImapScanner

    monkeypatch.setattr(es, "IMAP_FETCH_WORKERS", 4)
    monkeypatch.setattr(es, "IMAP_PARALLEL_THRESHOLD", 3)

    worker_called = {"n": 0}
    def never_call_worker(*args, **kwargs):  # pragma: no cover
        del args, kwargs
        worker_called["n"] += 1
        return [], ""
    monkeypatch.setattr(es, "_fetch_folder_worker", never_call_worker)

    all_msgs = [
        SimpleNamespace(uid=str(i), subject=f"s{i}", text="", html="", from_=f"u{i}@qq.com",
                        date="2024-01-01T00:00:00Z", attachments=[], headers={"Message-ID": f"<m{i}@qq.com>"})
        for i in range(1, 11)
    ]

    class QqMBX:
        def __init__(self, host, port, **kwargs):
            del host, port, kwargs
            self._folder = _FakeFolderManager()

        @property
        def folder(self):
            return self._folder

        def login(self, u, p, **kwargs):
            del u, p, kwargs
            return _FakeContextManager(self)

        def uids(self, criteria):
            del criteria
            return [str(i) for i in range(1, 11)]

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only
            assert bulk == es.QQ_BULK_SIZE, f"QQ must use bulk={es.QQ_BULK_SIZE}, got {bulk}"
            return iter(all_msgs)

    monkeypatch.setattr(es, "MailBox", QqMBX)
    account = EmailAccount(
        id=1, name="qq-account", type="qq", host="imap.qq.com", port=993,
        username="user@qq.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    emails = ImapScanner()._scan_sync(account, None)
    assert worker_called["n"] == 0, "QQ must NOT invoke _fetch_folder_worker (parallel path)"
    assert len(emails) == 10


def test_qq_scan_applies_inter_batch_sleep_and_noop_keepalive(
    monkeypatch: pytest.MonkeyPatch, settings
) -> None:
    """QQ rate-limits aggressively. The scan must (a) sleep between bulk
    batches and (b) send a NOOP every QQ_NOOP_EVERY_N_BATCHES to keep the
    connection alive and avoid server-side idle drop. Enough messages to
    trigger at least one NOOP window."""
    from app.services import email_scanner as es
    from app.services.email_scanner import ImapScanner

    monkeypatch.setattr(es, "QQ_INTER_BATCH_SLEEP_SECONDS", 0)
    monkeypatch.setattr(es, "QQ_NOOP_EVERY_N_BATCHES", 2)
    sleeps: list[float] = []
    monkeypatch.setattr(es.time, "sleep", lambda s: sleeps.append(s))

    msg_count = es.QQ_BULK_SIZE * 4 + 3
    all_msgs = [
        SimpleNamespace(uid=str(i), subject=f"s{i}", text="", html="", from_=f"u{i}@qq.com",
                        date="2024-01-01T00:00:00Z", attachments=[], headers={"Message-ID": f"<m{i}@qq.com>"})
        for i in range(1, msg_count + 1)
    ]
    noop_calls: list[bool] = []

    class FakeClient:
        sock = None
        def noop(self):
            noop_calls.append(True)
            return ("OK", [b"NOOP completed"])

    class QqMBX:
        def __init__(self, host, port, **kwargs):
            del host, port, kwargs
            self._folder = _FakeFolderManager()
            self.client = FakeClient()

        @property
        def folder(self):
            return self._folder

        def login(self, u, p, **kwargs):
            del u, p, kwargs
            return _FakeContextManager(self)

        def uids(self, criteria):
            del criteria
            return [str(i) for i in range(1, msg_count + 1)]

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            return iter(all_msgs)

    monkeypatch.setattr(es, "MailBox", QqMBX)
    account = EmailAccount(
        id=1, name="qq-account", type="qq", host="imap.qq.com", port=993,
        username="user@qq.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    emails = ImapScanner()._scan_sync(account, None)
    assert len(emails) == msg_count
    assert len(sleeps) >= 1, "QQ must sleep between batches to respect rate limit"
    assert len(noop_calls) >= 1, "QQ must send NOOP keepalive to prevent idle drop"


def test_qq_scan_uid_range_search_skips_already_seen_uids(
    monkeypatch: pytest.MonkeyPatch, settings
) -> None:
    """On incremental QQ scans with a saved highest-UID baseline, the
    fetch criteria must be AND(uid=U(last+1,'*')) so QQ's server only
    searches new messages. Without this, a bare SEARCH ALL on a 35k
    mailbox times out."""
    from app.services import email_scanner as es
    from app.services.email_scanner import ImapScanner, _serialize_imap_state

    captured_criteria: list = []
    all_msgs = [
        SimpleNamespace(uid=str(i), subject=f"s{i}", text="", html="", from_=f"u{i}@qq.com",
                        date="2024-01-01T00:00:00Z", attachments=[], headers={"Message-ID": f"<m{i}@qq.com>"})
        for i in [101, 102, 103]
    ]

    class QqMBX:
        def __init__(self, host, port, **kwargs):
            del host, port, kwargs
            self._folder = _FakeFolderManager(
                folders=[_FakeFolderInfo("INBOX")],
                uidvalidity_map={"INBOX": 42},
                uidnext_map={"INBOX": 104},
                messages_map={"INBOX": 103},
            )
            self.client = None

        @property
        def folder(self):
            return self._folder

        def login(self, u, p, **kwargs):
            del u, p, kwargs
            return _FakeContextManager(self)

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del limit, reverse, mark_seen, headers_only, bulk
            captured_criteria.append(str(criteria))
            return iter(all_msgs)

    monkeypatch.setattr(es, "MailBox", QqMBX)
    prev_state = _serialize_imap_state({
        "INBOX": {"uid": "100", "uidvalidity": "42", "uidnext": "101", "messages": "100"},
    })
    account = EmailAccount(
        id=1, name="qq-account", type="qq", host="imap.qq.com", port=993,
        username="user@qq.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    _ = ImapScanner()._scan_sync(account, prev_state)
    assert captured_criteria, "fetch must have been called"
    crit_str = captured_criteria[0]
    assert "101:*" in crit_str or "UID 101" in crit_str, (
        f"QQ incremental scan must restrict SEARCH to UID 101:*; got {crit_str!r}"
    )


def test_qq_scan_noop_failure_is_logged_and_loop_continues(
    monkeypatch: pytest.MonkeyPatch, settings, caplog
) -> None:
    """If the periodic NOOP keepalive raises (connection already dead),
    we must log a warning and let the natural fetch-loop error handling
    terminate the folder rather than crash the whole account scan."""
    from app.services import email_scanner as es
    from app.services.email_scanner import ImapScanner
    import logging

    monkeypatch.setattr(es, "QQ_INTER_BATCH_SLEEP_SECONDS", 0)
    monkeypatch.setattr(es, "QQ_NOOP_EVERY_N_BATCHES", 1)
    monkeypatch.setattr(es.time, "sleep", lambda _s: None)

    msg_count = es.QQ_BULK_SIZE * 2 + 1
    all_msgs = [
        SimpleNamespace(uid=str(i), subject=f"s{i}", text="", html="", from_=f"u{i}@qq.com",
                        date="2024-01-01T00:00:00Z", attachments=[], headers={"Message-ID": f"<m{i}@qq.com>"})
        for i in range(1, msg_count + 1)
    ]

    class FlakyClient:
        sock = None
        def noop(self):
            raise OSError("connection reset by peer during noop")

    class QqMBX:
        def __init__(self, host, port, **kwargs):
            del host, port, kwargs
            self._folder = _FakeFolderManager()
            self.client = FlakyClient()

        @property
        def folder(self):
            return self._folder

        def login(self, u, p, **kwargs):
            del u, p, kwargs
            return _FakeContextManager(self)

        def uids(self, criteria):
            del criteria
            return [str(i) for i in range(1, msg_count + 1)]

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria, limit, reverse, mark_seen, headers_only, bulk
            return iter(all_msgs)

    monkeypatch.setattr(es, "MailBox", QqMBX)
    account = EmailAccount(
        id=1, name="qq-account", type="qq", host="imap.qq.com", port=993,
        username="user@qq.com",
        password_encrypted=encrypt_password("secret", settings.JWT_SECRET),
    )
    with caplog.at_level(logging.WARNING, logger="app.services.email_scanner"):
        emails = ImapScanner()._scan_sync(account, None)
    assert any("NOOP keepalive failed" in rec.message for rec in caplog.records), (
        "failed NOOP must produce a warning log line"
    )
    assert len(emails) == msg_count, (
        "a failed NOOP must not truncate the fetch iterator; messages already iterated must be kept"
    )
