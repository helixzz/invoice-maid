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
    def __init__(self, folders=None, uidvalidity_map=None):
        self._folders = folders if folders is not None else [_FakeFolderInfo("INBOX")]
        self._uidvalidity_map = uidvalidity_map or {}
        self.current: str = "INBOX"

    def list(self):
        return self._folders

    def set(self, name: str) -> None:
        self.current = name

    def status(self, name: str, items) -> dict:
        del items
        return {"UIDVALIDITY": self._uidvalidity_map.get(name, 12345)}


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
        def __init__(self, host, port):
            self.host = host
            self.port = port
            self.folder = _FakeFolderManager()

        def login(self, username, password):
            assert username == "user@example.com"
            assert password == "secret"
            return _FakeContextManager(self)

        def fetch(self, criteria, limit=None, reverse=False, mark_seen=True, headers_only=False, bulk=False):
            del criteria
            assert limit is None
            assert reverse is True
            assert mark_seen is False
            assert headers_only is True
            assert bulk == 100
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
        def __init__(self, host, port):
            del host, port
            self.folder = _FakeFolderManager()

        def login(self, username, password):
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
        "bulk": 100,
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
        def __init__(self, host, port):
            del host, port
            self.folder = _FakeFolderManager()

        def login(self, username, password):
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
        def __init__(self, host, port):
            del host, port

        def login(self, username, password):
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
        def __init__(self, host, port):
            del host, port
            self.folder = _FakeFolderManager()

        def login(self, username, password):
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
        def __init__(self, host, port):
            del host, port

        def login(self, username, password):
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
    monkeypatch.setattr(ImapScanner, "_scan_sync", lambda self, account, last_uid: ["imap"])
    monkeypatch.setattr(Pop3Scanner, "_scan_sync", lambda self, account, last_uid: ["pop3"])
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
        def __init__(self, host, port):
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
        def __init__(self, host, port):
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
        def __init__(self, host, port):
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
        def __init__(self, host, port):
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
        def __init__(self, host, port):
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
        def __init__(self, host, port):
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
        f"{email_scanner.GRAPH_BASE_URL}/me/mailFolders?$top=100",
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

    # _parse_imap_state: valid new JSON dict-of-dicts
    raw = json.dumps({"INBOX": {"uid": "5", "uidvalidity": "12345"}, "Archive": {"uid": "2", "uidvalidity": "99"}})
    result = _parse_imap_state(raw)
    assert result["INBOX"] == {"uid": "5", "uidvalidity": "12345"}
    assert result["Archive"] == {"uid": "2", "uidvalidity": "99"}

    # _parse_imap_state: invalid JSON -> falls back to legacy bare string
    assert _parse_imap_state("notjson") == {"INBOX": {"uid": "notjson", "uidvalidity": ""}}

    # _parse_imap_state: valid JSON but not dict-of-dicts -> falls back
    assert _parse_imap_state(json.dumps(["a", "b"])) == {"INBOX": {"uid": '["a", "b"]', "uidvalidity": ""}}

    # _parse_imap_state: None -> empty
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
        def __init__(self, host, port):
            del host, port
            self.folder = _FakeFolderManager(folders=folders)

        def login(self, username, password):
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
        def __init__(self, host, port):
            del host, port
            self.folder = _FakeFolderManager(folders=[_FakeFolderInfo("INBOX")], uidvalidity_map={"INBOX": 99999})

        def login(self, username, password):
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
        def __init__(self, host, port):
            del host, port
            self.folder = _FakeFolderManager(folders=[
                _FakeFolderInfo("INBOX"),
                _FakeFolderInfo("BadFolder"),
            ])
            self._set_count = 0

        def login(self, username, password):
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
        def __init__(self, host, port):
            del host, port
            self._folder = BadFolderManager(folders=[
                _FakeFolderInfo("INBOX"),
                _FakeFolderInfo("BadFolder"),
            ])
        @property
        def folder(self):
            return self._folder

        def login(self, username, password):
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
        def __init__(self, host, port):
            del host, port
            self._folder = _FakeFolderManager(folders=[_FakeFolderInfo("INBOX")])

        @property
        def folder(self):
            return self._folder

        def login(self, username, password):
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
        def __init__(self, host, port):
            del host, port
            self._folder = _FakeFolderManager()

        @property
        def folder(self):
            return self._folder

        def login(self, username, password):
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
        def __init__(self, host, port):
            del host, port
            self._folder = HydrateFolderManager()

        @property
        def folder(self):
            return self._folder

        def login(self, username, password):
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
        def __init__(self, host, port):
            del host, port
            self._folder = StatusErrorManager(folders=[_FakeFolderInfo("INBOX")])

        @property
        def folder(self):
            return self._folder

        def login(self, username, password):
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
        def __init__(self, host, port):
            del host, port
            self._folder = _FakeFolderManager(
                folders=[_FakeFolderInfo("INBOX")],
                uidvalidity_map={"INBOX": 77777},
            )

        @property
        def folder(self):
            return self._folder

        def login(self, username, password):
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
    assert urls_seen.count(f"{GRAPH_BASE_URL}/me/mailFolders?$top=100") == 1


@pytest.mark.asyncio
async def test_imap_hydrate_folder_set_failure_returns_empty(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    from app.services.email_scanner import ImapScanner, RawEmail
    from app.services import email_scanner
    from imap_tools.errors import MailboxLoginError

    class SetErrorFolderManager(_FakeFolderManager):
        def set(self, name):
            raise MailboxLoginError("folder_set_error", 403)

    class SetErrorMailbox:
        def __init__(self, host, port):
            del host, port
            self._folder = SetErrorFolderManager()

        @property
        def folder(self):
            return self._folder

        def login(self, username, password):
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
        def __init__(self, host, port):
            del host, port
            self.folder = _FakeFolderManager()

        def login(self, username, password):
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
        def __init__(self, host, port):
            del host, port
            self._folder = _FakeFolderManager(folders=folders_list)

        @property
        def folder(self):
            return self._folder

        def login(self, username, password):
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
        def __init__(self, host, port):
            del host, port
            self._folder = _FakeFolderManager(folders=[
                _FakeFolderInfo("INBOX"),
                _FakeFolderInfo("Archive"),
            ])
            self._inbox_done = False

        @property
        def folder(self):
            return self._folder

        def login(self, username, password):
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
