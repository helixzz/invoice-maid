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


def test_imap_scan_sync_filters_attachments_and_last_uid(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    msg_old = SimpleNamespace(uid="1", subject="old", text="see https://skip.test", html="", from_="a@test", date=datetime.now(), attachments=[])
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
    )

    class FakeMailbox:
        def __init__(self, host, port):
            self.host = host
            self.port = port

        def login(self, username, password):
            assert username == "user@example.com"
            assert password == "secret"
            return _FakeContextManager(self)

        def fetch(self, criteria, limit, reverse):
            del criteria
            assert limit == 200
            assert reverse is True
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
    assert emails[0].body_links == ["https://invoice.test"]
    assert emails[0].attachments[0].filename == "invoice.pdf"


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
            if url.endswith("/messages"):
                return FakeResponse(
                    {
                        "value": [
                            {
                                "id": "graph-1",
                                "internetMessageId": "uid-1",
                                "subject": "Invoice",
                                "body": {"contentType": "html", "content": "<p>hello https://invoice.test</p>"},
                                "from": {"emailAddress": {"address": "sender@test"}},
                                "receivedDateTime": "2024-01-01T00:00:00Z",
                            }
                        ],
                        "@odata.nextLink": None,
                    }
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
    assert emails[0].attachments[0].filename == "invoice.pdf"
    assert emails[0].body_links == ["https://invoice.test"]
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
async def test_outlook_scan_last_uid_and_200_limit(monkeypatch: pytest.MonkeyPatch, tmp_path, settings) -> None:
    scanner = OutlookScanner()
    account = EmailAccount(id=4, name="outlook", type="outlook", username="client-id", oauth_token_path=str(tmp_path / "cache.json"))

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    items = [
        {"id": f"id-{idx}", "internetMessageId": f"uid-{idx}", "subject": "Invoice", "body": {"contentType": "text", "content": "body"}, "from": {"emailAddress": {"address": "a@test"}}, "receivedDateTime": "2024-01-01T00:00:00Z"}
        for idx in range(205)
    ]

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

        async def get(self, url, headers=None, params=None):
            del url, headers
            if params is None:
                return FakeResponse({"value": []})
            if params and params.get("$top") == "100":
                return FakeResponse({"value": items, "@odata.nextLink": None})
            return FakeResponse({"value": []})

    monkeypatch.setattr(email_scanner.httpx, "AsyncClient", lambda timeout: FakeClient())
    monkeypatch.setattr(scanner, "_acquire_access_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(scanner, "_fetch_attachments", AsyncMock(return_value=[]))
    emails = await scanner.scan(account, last_uid="other")
    assert len(emails) == 200

    class EarlyReturnClient(FakeClient):
        async def get(self, url, headers=None, params=None):
            del url, headers, params
            return FakeResponse({"value": [{"id": "graph", "internetMessageId": "uid-1", "subject": "x", "body": {"contentType": "text", "content": "body"}, "from": {"emailAddress": {"address": "a@test"}}, "receivedDateTime": "2024-01-01T00:00:00Z"}], "@odata.nextLink": None})

    monkeypatch.setattr(email_scanner.httpx, "AsyncClient", lambda timeout: EarlyReturnClient())
    assert await scanner.scan(account, last_uid="uid-1") == []


@pytest.mark.asyncio
async def test_outlook_access_token_and_attachment_helpers(monkeypatch: pytest.MonkeyPatch, tmp_path, settings) -> None:
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


def test_outlook_token_cache_and_sync_flow(monkeypatch: pytest.MonkeyPatch, tmp_path, settings) -> None:
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
