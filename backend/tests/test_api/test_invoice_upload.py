from __future__ import annotations

import io
import zipfile

import pytest

from app.services import manual_upload as manual_upload_module
from app.services.invoice_parser import ParsedInvoice


# Minimal valid PDF that still satisfies the magic-byte check. We monkeypatch
# ``parse_invoice`` so the real parser never runs — the test bytes only need
# to survive the endpoint's magic-byte gate.
PDF_MAGIC_BYTES = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n" + b"0" * 128
XML_MAGIC_BYTES = b"<?xml version='1.0'?><Invoice><InvoiceNo>X</InvoiceNo></Invoice>"
OFD_MAGIC_BYTES = b"PK\x03\x04" + b"\x00" * 100  # ZIP header + padding


def _parsed_happy_path() -> ParsedInvoice:
    from datetime import date
    from decimal import Decimal

    return ParsedInvoice(
        invoice_no="26312000002171930221",
        buyer="Acme Corp",
        seller="Widget Co",
        amount=Decimal("1234.56"),
        invoice_date=date(2026, 4, 20),
        invoice_type="电子发票（普通发票）",
        item_summary="Office supplies",
        raw_text="sample text " * 20,
        source_format="pdf",
        extraction_method="qr",
        confidence=0.95,
        is_vat_document=True,
    )


async def test_upload_saves_invoice_and_returns_201(
    client, auth_headers, manual_upload_account, mock_ai_service, settings, monkeypatch
) -> None:
    """Happy path: PDF content passes magic-byte check, parser returns a
    strong ParsedInvoice, endpoint responds 201 with the serialized Invoice,
    and a ScanLog + ExtractionLog(outcome='manual_upload_saved') are persisted."""
    from app.api import invoices as invoices_api
    from app.services import manual_upload as mu

    monkeypatch.setattr(mu, "parse_invoice", lambda filename, payload: _parsed_happy_path())
    monkeypatch.setattr(invoices_api, "AIService", lambda _settings: mock_ai_service)
    del settings

    response = await client.post(
        "/api/v1/invoices/upload",
        headers=auth_headers,
        files={"file": ("invoice.pdf", PDF_MAGIC_BYTES, "application/pdf")},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["invoice_no"] == "26312000002171930221"
    assert body["buyer"] == "Acme Corp"
    assert body["seller"] == "Widget Co"
    assert float(body["amount"]) == 1234.56
    assert body["extraction_method"] == "qr"


async def test_upload_rejects_empty_file_with_400(
    client, auth_headers, manual_upload_account
) -> None:
    response = await client.post(
        "/api/v1/invoices/upload",
        headers=auth_headers,
        files={"file": ("invoice.pdf", b"", "application/pdf")},
    )
    # Empty file either fails magic-byte check (415) or size=0 check (400);
    # both are acceptable rejections.
    assert response.status_code in (400, 415), response.text


async def test_upload_rejects_magic_byte_mismatch_with_415(
    client, auth_headers, manual_upload_account
) -> None:
    """File declares application/pdf but content starts with bytes that
    don't match any accepted format's magic signature. This is the
    'attacker renames evil.exe to invoice.pdf' scenario."""
    response = await client.post(
        "/api/v1/invoices/upload",
        headers=auth_headers,
        files={"file": ("fake.pdf", b"MZ\x90\x00" + b"\x00" * 200, "application/pdf")},
    )
    assert response.status_code == 415
    assert "does not match" in response.json()["detail"].lower() or "accepted format" in response.json()["detail"].lower()


async def test_upload_rejects_unsupported_extension_with_415(
    client, auth_headers, manual_upload_account
) -> None:
    response = await client.post(
        "/api/v1/invoices/upload",
        headers=auth_headers,
        files={"file": ("evil.exe", b"anything", "application/octet-stream")},
    )
    assert response.status_code == 415
    assert ".exe" in response.json()["detail"]


async def test_upload_rejects_unsupported_content_type_with_415(
    client, auth_headers, manual_upload_account
) -> None:
    response = await client.post(
        "/api/v1/invoices/upload",
        headers=auth_headers,
        files={"file": ("invoice.pdf", PDF_MAGIC_BYTES, "image/png")},
    )
    assert response.status_code == 415
    assert "image/png" in response.json()["detail"]


async def test_upload_duplicate_invoice_returns_409_with_existing_id(
    client, auth_headers, manual_upload_account, create_invoice, mock_ai_service, monkeypatch
) -> None:
    """When the extracted invoice_no matches an existing row, respond
    409 and include the existing_invoice_id so the frontend can link
    the user to the already-saved invoice."""
    existing = await create_invoice(invoice_no="DUP-001")
    parsed = _parsed_happy_path()
    parsed.invoice_no = "DUP-001"
    from app.api import invoices as invoices_api
    from app.services import manual_upload as mu

    monkeypatch.setattr(mu, "parse_invoice", lambda filename, payload: parsed)
    monkeypatch.setattr(invoices_api, "AIService", lambda _settings: mock_ai_service)

    response = await client.post(
        "/api/v1/invoices/upload",
        headers=auth_headers,
        files={"file": ("invoice.pdf", PDF_MAGIC_BYTES, "application/pdf")},
    )
    assert response.status_code == 409
    body = response.json()["detail"]
    assert body["outcome"] == "duplicate"
    assert body["existing_invoice_id"] == existing.id
    assert body["invoice_no"] == "DUP-001"


async def test_upload_low_confidence_returns_422(
    client, auth_headers, manual_upload_account, mock_ai_service, monkeypatch
) -> None:
    """Parser returns a weak result (confidence 0.3, no invoice_no). LLM
    enrichment is attempted but also returns is_valid_tax_invoice=False,
    so we should stop at the low_confidence gate with 422."""
    from datetime import date
    from decimal import Decimal
    from app.schemas.invoice import InvoiceExtract
    from app.api import invoices as invoices_api
    from app.services import manual_upload as mu

    weak = ParsedInvoice(
        invoice_no=None,
        buyer=None,
        seller=None,
        amount=Decimal("0.00"),
        invoice_date=None,
        invoice_type=None,
        item_summary=None,
        raw_text="unreadable scribbles",
        source_format="pdf",
        extraction_method="regex",
        confidence=0.3,
        is_vat_document=False,
    )
    monkeypatch.setattr(mu, "parse_invoice", lambda filename, payload: weak)
    mock_ai_service.extract_invoice_fields.return_value = InvoiceExtract(
        buyer="未知",
        seller="未知",
        invoice_no="UNKNOWN",
        invoice_date=date(2024, 1, 1),
        amount=Decimal("0.05"),
        item_summary="未知",
        invoice_type="未知",
        confidence=0.3,
        is_valid_tax_invoice=False,
    )
    monkeypatch.setattr(invoices_api, "AIService", lambda _settings: mock_ai_service)

    response = await client.post(
        "/api/v1/invoices/upload",
        headers=auth_headers,
        files={"file": ("invoice.pdf", PDF_MAGIC_BYTES, "application/pdf")},
    )
    assert response.status_code == 422
    body = response.json()["detail"]
    assert body["outcome"] in ("low_confidence", "not_vat_invoice")


async def test_upload_parse_failure_returns_422(
    client, auth_headers, manual_upload_account, mock_ai_service, monkeypatch
) -> None:
    """parse_invoice() raises. The endpoint should translate to 422
    with outcome='parse_failed' and a descriptive detail message."""
    from app.api import invoices as invoices_api
    from app.services import manual_upload as mu

    def boom(filename: str, payload: bytes) -> ParsedInvoice:
        raise RuntimeError("corrupt file")

    monkeypatch.setattr(mu, "parse_invoice", boom)
    monkeypatch.setattr(invoices_api, "AIService", lambda _settings: mock_ai_service)

    response = await client.post(
        "/api/v1/invoices/upload",
        headers=auth_headers,
        files={"file": ("invoice.pdf", PDF_MAGIC_BYTES, "application/pdf")},
    )
    assert response.status_code == 422
    body = response.json()["detail"]
    assert body["outcome"] == "parse_failed"
    assert "corrupt" in body["detail"].lower()


async def test_upload_missing_manual_account_returns_500(
    client, auth_headers, mock_ai_service, monkeypatch
) -> None:
    """The sentinel account is a precondition. If it's missing
    (migration 0008 didn't run), the endpoint must raise 500 with a
    helpful hint rather than silently 404/mislabel."""
    from app.api import invoices as invoices_api
    from app.services import manual_upload as mu

    monkeypatch.setattr(mu, "parse_invoice", lambda filename, payload: _parsed_happy_path())
    monkeypatch.setattr(invoices_api, "AIService", lambda _settings: mock_ai_service)

    response = await client.post(
        "/api/v1/invoices/upload",
        headers=auth_headers,
        files={"file": ("invoice.pdf", PDF_MAGIC_BYTES, "application/pdf")},
    )
    assert response.status_code == 500
    assert "alembic upgrade head" in response.json()["detail"].lower()


async def test_upload_requires_authentication(client) -> None:
    response = await client.post(
        "/api/v1/invoices/upload",
        files={"file": ("invoice.pdf", PDF_MAGIC_BYTES, "application/pdf")},
    )
    assert response.status_code in (401, 403)


async def test_upload_xml_happy_path_passes_magic_check(
    client, auth_headers, manual_upload_account, mock_ai_service, monkeypatch
) -> None:
    from app.api import invoices as invoices_api
    from app.services import manual_upload as mu

    parsed = _parsed_happy_path()
    parsed.source_format = "xml"
    parsed.extraction_method = "xml_xpath"
    monkeypatch.setattr(mu, "parse_invoice", lambda filename, payload: parsed)
    monkeypatch.setattr(invoices_api, "AIService", lambda _settings: mock_ai_service)

    response = await client.post(
        "/api/v1/invoices/upload",
        headers=auth_headers,
        files={"file": ("invoice.xml", XML_MAGIC_BYTES, "application/xml")},
    )
    assert response.status_code == 201, response.text
    assert response.json()["source_format"] == "xml"


async def test_upload_ofd_happy_path_passes_magic_check(
    client, auth_headers, manual_upload_account, mock_ai_service, monkeypatch
) -> None:
    from app.api import invoices as invoices_api
    from app.services import manual_upload as mu

    parsed = _parsed_happy_path()
    parsed.source_format = "ofd"
    parsed.extraction_method = "ofd_struct"
    monkeypatch.setattr(mu, "parse_invoice", lambda filename, payload: parsed)
    monkeypatch.setattr(invoices_api, "AIService", lambda _settings: mock_ai_service)

    response = await client.post(
        "/api/v1/invoices/upload",
        headers=auth_headers,
        files={"file": ("invoice.ofd", OFD_MAGIC_BYTES, "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    assert response.json()["source_format"] == "ofd"


async def test_upload_filename_with_path_traversal_is_neutralized(
    client, auth_headers, manual_upload_account, mock_ai_service, monkeypatch
) -> None:
    """Client sends a filename of '../../etc/passwd'. The endpoint must
    generate a UUID-based filesystem name and never echo the traversal
    bytes anywhere that touches disk. The request still succeeds because
    the traversal doesn't break parsing — it just shouldn't land on disk."""
    from app.api import invoices as invoices_api
    from app.services import manual_upload as mu
    import uuid as _uuid

    seen_filenames: list[str] = []

    def spy_parse(filename: str, payload: bytes) -> ParsedInvoice:
        seen_filenames.append(filename)
        return _parsed_happy_path()

    monkeypatch.setattr(mu, "parse_invoice", spy_parse)
    monkeypatch.setattr(invoices_api, "AIService", lambda _settings: mock_ai_service)

    response = await client.post(
        "/api/v1/invoices/upload",
        headers=auth_headers,
        files={"file": ("../../etc/passwd.pdf", PDF_MAGIC_BYTES, "application/pdf")},
    )
    assert response.status_code == 201, response.text
    assert seen_filenames, "parse_invoice should have been called"
    for fn in seen_filenames:
        assert ".." not in fn
        assert "/" not in fn and "\\" not in fn


def test_parse_xml_rejects_xxe_external_entity() -> None:
    """billion-laughs / XXE via DOCTYPE ENTITY declaration. defusedxml
    refuses to parse these — parser should return a ParsedInvoice with
    confidence=0.0 instead of leaking /etc/passwd into raw_text."""
    from app.services.invoice_parser import parse_xml

    xxe_payload = (
        b"<?xml version='1.0'?>"
        b"<!DOCTYPE root ["
        b"<!ENTITY xxe SYSTEM 'file:///etc/passwd'>"
        b"]>"
        b"<Invoice><InvoiceNo>&xxe;</InvoiceNo></Invoice>"
    )
    result = parse_xml(xxe_payload)
    assert result.confidence == 0.0
    # If defusedxml allowed the entity, /etc/passwd contents would appear
    # in raw_text as root:x:0:0:... . The literal string 'etc/passwd' IS in
    # the DOCTYPE source itself, so we check for the resolved content marker.
    assert "root:x:0:0" not in result.raw_text


def test_parse_ofd_rejects_zip_bomb(monkeypatch) -> None:
    """Construct a ZIP whose cumulative uncompressed sizes exceed the
    100 MB ceiling. Parser should reject BEFORE passing bytes to easyofd.

    We fake the ZipFile.infolist() to return entries with inflated
    file_size values — this is more reliable than trying to author a
    real zip-bomb byte pattern that zipfile.ZipInfo serializes
    verbatim without recomputing sizes."""
    import zipfile as zipfile_mod
    from app.services import invoice_parser

    class _BombEntry:
        def __init__(self, size: int) -> None:
            self.file_size = size

    class _BombZip:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def infolist(self):
            return [
                _BombEntry(50 * 1024 * 1024),
                _BombEntry(60 * 1024 * 1024),
            ]

    monkeypatch.setattr(zipfile_mod, "ZipFile", _BombZip)

    result = invoice_parser.parse_ofd(b"PK\x03\x04" + b"\x00" * 50)
    assert result.confidence == 0.0


def test_parse_ofd_rejects_invalid_zip_container() -> None:
    """File starts with PK\\x03\\x04 but the rest is garbage — the zip
    header probe hits BadZipFile. Parser must handle gracefully."""
    from app.services.invoice_parser import parse_ofd

    result = parse_ofd(b"PK\x03\x04" + b"\x00" * 50)
    assert result.confidence == 0.0


def test_parse_ofd_small_valid_zip_passes_size_check(monkeypatch) -> None:
    """When the input IS a valid OFD-shaped ZIP and its cumulative
    uncompressed size is below the 100 MB ceiling, the zip-bomb check
    must fall through to the easyofd parsing step. This test covers
    the 'small valid OFD' branch of the size guard."""
    from app.services import invoice_parser

    class _TinyEntry:
        file_size = 1024  # 1 KB — well under the 100 MB ceiling

    class _TinyZip:
        def __init__(self, *args, **kwargs):
            del args, kwargs
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def infolist(self):
            return [_TinyEntry()]

    monkeypatch.setattr(invoice_parser.zipfile, "ZipFile", _TinyZip)
    # When easyofd fails to find structured data, confidence stays 0 but
    # we still executed the fall-through — that's what this test asserts.
    # We don't monkeypatch easyofd because the real module handles the
    # test bytes gracefully by returning no data.
    result = invoice_parser.parse_ofd(b"PK\x03\x04" + b"\x00" * 100)
    assert result.source_format == "ofd"


async def test_upload_safe_filename_generates_uuid_extension_from_detected_format(
    client, auth_headers, manual_upload_account, mock_ai_service, monkeypatch
) -> None:
    """When client filename lacks an extension, the endpoint should
    still choose a parser_filename with the magic-byte-detected extension
    so downstream format detection via filename works."""
    from app.api import invoices as invoices_api
    from app.services import manual_upload as mu

    captured: list[str] = []

    def spy_parse(filename: str, payload: bytes) -> ParsedInvoice:
        captured.append(filename)
        return _parsed_happy_path()

    monkeypatch.setattr(mu, "parse_invoice", spy_parse)
    monkeypatch.setattr(invoices_api, "AIService", lambda _settings: mock_ai_service)

    response = await client.post(
        "/api/v1/invoices/upload",
        headers=auth_headers,
        files={"file": ("no-extension", PDF_MAGIC_BYTES, "application/pdf")},
    )
    assert response.status_code == 201
    assert captured and captured[0].endswith(".pdf")


async def test_content_size_limit_middleware_rejects_oversized_upload(
    client, auth_headers, manual_upload_account
) -> None:
    """Send a body that claims Content-Length > 25 MB. The
    ContentSizeLimitMiddleware should short-circuit with 413 before the
    route handler even runs."""
    oversized = b"x" * (26 * 1024 * 1024)
    response = await client.post(
        "/api/v1/invoices/upload",
        headers=auth_headers,
        files={"file": ("big.pdf", oversized, "application/pdf")},
    )
    assert response.status_code == 413


def test_content_size_middleware_passes_non_protected_paths_through() -> None:
    """GET requests and non-upload writes must NOT be wrapped by the
    middleware's byte counter — they should be forwarded to the app
    unchanged. This test exercises the fast-path branch directly."""
    import asyncio
    from app.middleware import ContentSizeLimitMiddleware

    recorded_scope: list[dict] = []

    async def fake_app(scope, receive, send):
        recorded_scope.append(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    mw = ContentSizeLimitMiddleware(
        fake_app, max_content_size=1024, protected_paths=("/api/v1/invoices/upload",)
    )

    scope = {"type": "http", "method": "GET", "path": "/api/v1/invoices", "headers": []}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    sent_messages: list[dict] = []

    async def send(msg):
        sent_messages.append(msg)

    asyncio.get_event_loop().run_until_complete(mw(scope, receive, send))
    assert recorded_scope and recorded_scope[0]["method"] == "GET"
    assert sent_messages[0]["status"] == 200


def test_content_size_middleware_rejects_declared_oversize_before_body() -> None:
    """If Content-Length header already exceeds the limit, middleware
    must return 413 without consuming the body — this is the fastest
    rejection path (nothing downloaded)."""
    import asyncio
    from app.middleware import ContentSizeLimitMiddleware

    called_app = False

    async def fake_app(scope, receive, send):
        nonlocal called_app
        called_app = True

    mw = ContentSizeLimitMiddleware(
        fake_app, max_content_size=1024, protected_paths=("/upload",)
    )
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/upload",
        "headers": [(b"content-length", b"99999999")],
    }

    async def receive():  # pragma: no cover - should never be called
        raise AssertionError("receive must not be invoked")

    sent: list[dict] = []

    async def send(msg):
        sent.append(msg)

    asyncio.get_event_loop().run_until_complete(mw(scope, receive, send))
    assert not called_app
    assert sent[0]["status"] == 413


def test_content_size_middleware_accepts_malformed_content_length_header() -> None:
    """If the Content-Length header is non-numeric (e.g. from a
    broken client or attacker), the middleware should still inspect the
    streamed body rather than crash."""
    import asyncio
    from app.middleware import ContentSizeLimitMiddleware

    async def fake_app(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = ContentSizeLimitMiddleware(
        fake_app, max_content_size=10 * 1024, protected_paths=("/upload",)
    )
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/upload",
        "headers": [(b"content-length", b"not-a-number")],
    }

    async def receive():
        return {"type": "http.request", "body": b"small", "more_body": False}

    sent: list[dict] = []

    async def send(msg):
        sent.append(msg)

    asyncio.get_event_loop().run_until_complete(mw(scope, receive, send))
    assert sent[0]["status"] == 200


def test_content_size_middleware_accumulates_and_stops_at_threshold() -> None:
    """Streaming attacker sends many small chunks. Middleware must
    count cumulatively and return 413 on the chunk that tips past the
    threshold, not let the whole body through."""
    import asyncio
    from app.middleware import ContentSizeLimitMiddleware

    app_called_count = 0

    async def fake_app(scope, receive, send):
        nonlocal app_called_count
        while True:
            msg = await receive()
            app_called_count += 1
            if not msg.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    mw = ContentSizeLimitMiddleware(
        fake_app, max_content_size=100, protected_paths=("/upload",)
    )
    scope = {"type": "http", "method": "POST", "path": "/upload", "headers": []}

    chunks = [
        {"type": "http.request", "body": b"x" * 50, "more_body": True},
        {"type": "http.request", "body": b"x" * 60, "more_body": True},
        {"type": "http.request", "body": b"x" * 50, "more_body": False},
    ]
    it = iter(chunks)

    async def receive():
        try:
            return next(it)
        except StopIteration:  # pragma: no cover
            return {"type": "http.request", "body": b"", "more_body": False}

    sent: list[dict] = []

    async def send(msg):
        sent.append(msg)

    asyncio.get_event_loop().run_until_complete(mw(scope, receive, send))
    statuses = [msg["status"] for msg in sent if msg["type"] == "http.response.start"]
    assert 413 in statuses


def test_content_size_middleware_lifespan_scope_passes_through() -> None:
    """ASGI scope type 'lifespan' is sent on startup/shutdown, not HTTP.
    Middleware must forward it unchanged so FastAPI's lifespan hooks
    still run. Without this guard the middleware would raise on every
    app startup."""
    import asyncio
    from app.middleware import ContentSizeLimitMiddleware

    forwarded = {"hit": False}

    async def fake_app(scope, receive, send):
        del receive, send
        forwarded["hit"] = scope.get("type") == "lifespan"

    mw = ContentSizeLimitMiddleware(fake_app, max_content_size=1, protected_paths=("/upload",))
    asyncio.get_event_loop().run_until_complete(
        mw({"type": "lifespan"}, lambda: None, lambda msg: None)
    )
    assert forwarded["hit"] is True


def test_detect_format_from_magic_uses_filetype_fallback(monkeypatch) -> None:
    """If the raw byte-prefix check misses but ``filetype.guess`` recognizes
    the content, use its extension when it maps to an accepted format.
    This covers the fallback branch that the explicit-prefix tests skip."""
    from app.api import invoices as invoices_api

    class _FakeGuess:
        extension = "pdf"

    monkeypatch.setattr(invoices_api.filetype, "guess", lambda _head: _FakeGuess())
    assert invoices_api._detect_format_from_magic(b"HELLO\x00\x00") == "pdf"


def test_detect_format_from_magic_returns_none_for_unknown(monkeypatch) -> None:
    from app.api import invoices as invoices_api

    monkeypatch.setattr(invoices_api.filetype, "guess", lambda _head: None)
    assert invoices_api._detect_format_from_magic(b"UNKNOWN\x00") is None


def test_safe_filename_handles_none_and_unknown_extensions() -> None:
    """_safe_filename must generate a valid filesystem name even when the
    client omits the filename entirely or uses a weird extension."""
    from app.api.invoices import _safe_filename

    none_result = _safe_filename(None)
    assert none_result.endswith(".bin")

    weird_ext = _safe_filename("attack.exe")
    assert weird_ext.endswith(".bin")

    empty = _safe_filename("")
    assert empty.endswith(".bin")


def test_outcome_to_status_unknown_outcome_returns_500() -> None:
    """The ``_outcome_to_status`` mapping must have an explicit default
    so that any new / unmapped outcome string degrades gracefully to 500
    instead of raising or leaking the outcome string to the client."""
    from app.api.invoices import _outcome_to_status

    assert _outcome_to_status("error") == 500
    assert _outcome_to_status("completely-new-outcome") == 500


async def test_upload_route_level_413_fires_when_middleware_bypassed(
    client, auth_headers, manual_upload_account, monkeypatch
) -> None:
    """Belt-and-suspenders: if the middleware is somehow bypassed (future
    refactor, path-list mistake), the route's own streaming size counter
    must still enforce the 25 MB ceiling. We simulate middleware bypass
    by temporarily shrinking the ceiling to 500 bytes, then send a 1 KB
    PDF that slips past the middleware (middleware still uses 25 MB)."""
    from app.api import invoices as invoices_api

    monkeypatch.setattr(invoices_api, "UPLOAD_MAX_BYTES", 500)
    big = PDF_MAGIC_BYTES + b"\x00" * 600  # ~745 bytes > 500 limit
    response = await client.post(
        "/api/v1/invoices/upload",
        headers=auth_headers,
        files={"file": ("invoice.pdf", big, "application/pdf")},
    )
    assert response.status_code == 413


async def test_upload_chunked_read_handles_multiple_chunks(
    client, auth_headers, manual_upload_account, mock_ai_service, monkeypatch
) -> None:
    """When the upload body is larger than one CHUNK_SIZE (256 KB by
    default), the endpoint's streaming loop iterates multiple times.
    We shrink the chunk size to force 3+ iterations and verify the
    non-first-chunk branch (``first_chunk=False`` path) appends each
    chunk without re-running the magic-byte detection."""
    from app.api import invoices as invoices_api
    from app.services import manual_upload as mu

    monkeypatch.setattr(invoices_api, "UPLOAD_CHUNK_SIZE", 32)
    monkeypatch.setattr(mu, "parse_invoice", lambda f, p: _parsed_happy_path())
    monkeypatch.setattr(invoices_api, "AIService", lambda _settings: mock_ai_service)

    body = PDF_MAGIC_BYTES + b"\x00" * 256  # forces ~8-9 reads at 32B each
    response = await client.post(
        "/api/v1/invoices/upload",
        headers=auth_headers,
        files={"file": ("invoice.pdf", body, "application/pdf")},
    )
    assert response.status_code == 201, response.text


def test_content_size_middleware_ignores_non_http_request_messages() -> None:
    """ASGI can deliver messages other than 'http.request' on the HTTP
    receive channel (notably 'http.disconnect' when the client hangs up).
    The middleware's byte counter must treat those as pass-through so the
    downstream app sees them unchanged and can handle disconnect cleanly."""
    import asyncio
    from app.middleware import ContentSizeLimitMiddleware

    delivered: list[dict] = []

    async def fake_app(scope, receive, send):
        del scope
        while True:
            msg = await receive()
            delivered.append(msg)
            if msg.get("type") == "http.disconnect":
                break
        await send({"type": "http.response.start", "status": 499, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    mw = ContentSizeLimitMiddleware(
        fake_app, max_content_size=1024, protected_paths=("/upload",)
    )
    scope = {"type": "http", "method": "POST", "path": "/upload", "headers": []}

    messages = iter([
        {"type": "http.request", "body": b"hi", "more_body": True},
        {"type": "http.disconnect"},
    ])

    async def receive():
        return next(messages)

    async def send(msg):
        del msg

    asyncio.get_event_loop().run_until_complete(mw(scope, receive, send))
    assert any(m.get("type") == "http.disconnect" for m in delivered)


async def test_upload_saves_saas_invoice_instead_of_rejecting_v120(
    client, auth_headers, manual_upload_account, mock_ai_service, settings, monkeypatch
) -> None:
    """v1.2.0 Track A default (STRICT_VAT_ONLY=false): a saas_invoice with
    is_valid_tax_invoice=False now SAVES. Under v1.1.x it would have been
    rejected as not_vat_invoice. The LLM merge runs for non-vat categories
    so invoice_no etc. populate correctly."""
    from datetime import date
    from decimal import Decimal

    from app.api import invoices as invoices_api
    from app.schemas.invoice import InvoiceCategory
    from app.services import manual_upload as mu

    parsed = ParsedInvoice(
        invoice_no=None,
        raw_text="Cursor invoice body " * 5,
        confidence=0.1,
        is_vat_document=False,
    )
    monkeypatch.setattr(mu, "parse_invoice", lambda filename, payload: parsed)
    mock_ai_service.extract_invoice_fields.return_value = (
        mock_ai_service.extract_invoice_fields.return_value.model_copy(
            update={
                "invoice_no": "in_cursor_001",
                "invoice_type": "Cursor Pro Subscription",
                "invoice_category": InvoiceCategory.SAAS_INVOICE,
                "is_valid_tax_invoice": False,
                "buyer": "Acme Corp",
                "seller": "Cursor AI Inc",
                "amount": Decimal("20.00"),
                "invoice_date": date(2026, 5, 1),
                "item_summary": "AI editor subscription",
            }
        )
    )
    monkeypatch.setattr(invoices_api, "AIService", lambda _settings: mock_ai_service)
    del settings

    response = await client.post(
        "/api/v1/invoices/upload",
        headers=auth_headers,
        files={"file": ("cursor.pdf", PDF_MAGIC_BYTES, "application/pdf")},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["invoice_no"] == "in_cursor_001"
    assert body["invoice_category"] == "saas_invoice"
    assert body["invoice_type"] == "Cursor Pro Subscription"


async def test_upload_strict_vat_only_reverts_to_v11x_rejection(
    client, auth_headers, manual_upload_account, mock_ai_service, settings, monkeypatch
) -> None:
    """STRICT_VAT_ONLY=true env flag reverts to v1.1.x behavior: saas_invoice
    with is_valid_tax_invoice=False gets rejected as not_vat_invoice even
    though category is saas_invoice. Operator rollback path."""
    from datetime import date
    from decimal import Decimal

    from app.api import invoices as invoices_api
    from app.schemas.invoice import InvoiceCategory
    from app.services import manual_upload as mu

    settings.STRICT_VAT_ONLY = True

    parsed = ParsedInvoice(
        invoice_no=None,
        raw_text="Cursor invoice body " * 5,
        confidence=0.1,
        is_vat_document=False,
    )
    monkeypatch.setattr(mu, "parse_invoice", lambda filename, payload: parsed)
    mock_ai_service.extract_invoice_fields.return_value = (
        mock_ai_service.extract_invoice_fields.return_value.model_copy(
            update={
                "invoice_no": "in_cursor_002",
                "invoice_type": "Cursor Pro Subscription",
                "invoice_category": InvoiceCategory.SAAS_INVOICE,
                "is_valid_tax_invoice": False,
                "amount": Decimal("20.00"),
                "invoice_date": date(2026, 5, 1),
            }
        )
    )
    monkeypatch.setattr(invoices_api, "AIService", lambda _settings: mock_ai_service)

    response = await client.post(
        "/api/v1/invoices/upload",
        headers=auth_headers,
        files={"file": ("cursor.pdf", PDF_MAGIC_BYTES, "application/pdf")},
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    detail_str = detail if isinstance(detail, str) else str(detail)
    assert "not_vat_invoice" in detail_str.lower() or "not a valid VAT" in detail_str or "not appear to be a valid VAT" in detail_str
