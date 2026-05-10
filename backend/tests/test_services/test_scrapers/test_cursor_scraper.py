from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.scrapers import cursor as cursor_mod
from app.services.scrapers.cursor import (
    CURSOR_BILLING_URL,
    SCRAPE_TIMEOUT_SECONDS,
    SEEN_INVOICE_IDS_CAP,
    CursorScraper,
)


def _make_locator(
    *,
    href: str | None = None,
    row_text: str | None = None,
) -> MagicMock:
    locator = MagicMock(name=f"locator[{href}]")
    locator.get_attribute = AsyncMock(return_value=href)
    locator.text_content = AsyncMock(return_value=row_text)

    row = MagicMock(name="row")
    row.is_visible = AsyncMock(return_value=row_text is not None)
    row.text_content = AsyncMock(return_value=row_text)
    locator.locator = MagicMock(return_value=row)
    return locator


def _make_response(body: bytes, status: int = 200) -> MagicMock:
    resp = MagicMock(name=f"response<{status}>")
    resp.status = status
    resp.body = AsyncMock(return_value=body)
    return resp


def _make_page(
    *,
    url_after_goto: str = CURSOR_BILLING_URL,
    invoice_links: list[MagicMock] | None = None,
    pdf_bytes_by_url: dict[str, bytes] | None = None,
    storage_state_payload: dict[str, Any] | None = None,
    request_get_side_effect: Any = None,
    wait_for_selector_side_effect: Any = None,
    goto_side_effect: Any = None,
) -> MagicMock:
    page = MagicMock(name="page")
    page.url = url_after_goto

    async def _goto(url: str, **_kwargs: Any) -> None:
        if goto_side_effect is not None:
            if isinstance(goto_side_effect, BaseException):
                raise goto_side_effect
            await goto_side_effect(url, **_kwargs)
        page.url = url_after_goto

    page.goto = AsyncMock(side_effect=_goto)
    page.wait_for_selector = AsyncMock(side_effect=wait_for_selector_side_effect)

    locators_by_selector: dict[str, MagicMock] = {}

    def _locator(selector: str) -> MagicMock:
        if selector not in locators_by_selector:
            top = MagicMock(name=f"top-locator[{selector}]")
            top.all = AsyncMock(return_value=invoice_links if invoice_links else [])
            locators_by_selector[selector] = top
        return locators_by_selector[selector]

    page.locator = MagicMock(side_effect=_locator)

    request = MagicMock(name="request")
    if request_get_side_effect is not None:
        request.get = AsyncMock(side_effect=request_get_side_effect)
    else:
        async def _request_get(url: str, **_kwargs: Any) -> MagicMock:
            payload = (pdf_bytes_by_url or {}).get(url, b"")
            return _make_response(payload)

        request.get = AsyncMock(side_effect=_request_get)

    context = MagicMock(name="context")
    context.request = request
    context.storage_state = AsyncMock(
        return_value=storage_state_payload
        or {"cookies": [{"name": "fresh"}], "origins": []}
    )
    page.context = context
    return page


class _FakeSession:
    def __init__(self, page: MagicMock, *, on_enter_exc: BaseException | None = None) -> None:
        self._page = page
        self._on_enter_exc = on_enter_exc
        self.updated_storage_state: str | None = None

    def __call__(self, account: Any) -> "_FakeSession":
        self._account = account
        return self

    async def __aenter__(self) -> MagicMock:
        if self._on_enter_exc is not None:
            raise self._on_enter_exc
        return self._page

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        del exc_type, exc, tb


def _make_account(storage_state: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        user_id=1,
        name="cursor-test",
        type="cursor",
        username="cursor-test",
        playwright_storage_state=storage_state,
        secondary_credential_encrypted=None,
        secondary_password_encrypted=None,
        totp_secret_encrypted=None,
    )


@pytest.fixture(autouse=True)
def _force_playwright_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cursor_mod, "PLAYWRIGHT_AVAILABLE", True, raising=False)


async def test_first_scan_with_empty_state_fetches_all_invoices() -> None:
    links = [
        _make_locator(
            href="https://pay.stripe.com/invoice/in_001/pdf",
            row_text="Apr 1, 2026 Pro Plan $20.00 USD Paid",
        ),
        _make_locator(
            href="https://pay.stripe.com/invoice/in_002/pdf",
            row_text="May 1, 2026 Pro Plan $20.00 USD Paid",
        ),
    ]
    page = _make_page(
        invoice_links=links,
        pdf_bytes_by_url={
            "https://pay.stripe.com/invoice/in_001/pdf": b"pdf-in_001",
            "https://pay.stripe.com/invoice/in_002/pdf": b"pdf-in_002",
        },
    )
    scraper = CursorScraper(session_cls=_FakeSession(page))
    progress: list[dict[str, Any]] = []

    result = await scraper.scan(
        _make_account(storage_state=json.dumps({"cookies": []})),
        last_uid=None,
        progress_callback=progress.append,
    )

    assert [e.uid for e in result] == ["cursor:in_001", "cursor:in_002"]
    assert result[0].attachments[0].payload == b"pdf-in_001"
    assert result[0].from_addr == "billing@cursor.com"
    assert result[0].headers["_scraper"] == "cursor"
    state = json.loads(scraper._last_scan_state or "{}")
    assert state["_format"] == "cursor_scraper_v1"
    assert state["seen_invoice_ids"] == ["in_001", "in_002"]
    assert len(progress) == 2
    page.goto.assert_awaited_once()


async def test_incremental_scan_filters_previously_seen_ids() -> None:
    links = [
        _make_locator(href="https://pay.stripe.com/invoice/in_001/pdf"),
        _make_locator(href="https://pay.stripe.com/invoice/in_002/pdf"),
    ]
    page = _make_page(
        invoice_links=links,
        pdf_bytes_by_url={
            "https://pay.stripe.com/invoice/in_001/pdf": b"x",
            "https://pay.stripe.com/invoice/in_002/pdf": b"y",
        },
    )
    scraper = CursorScraper(session_cls=_FakeSession(page))
    prior = json.dumps({
        "_format": "cursor_scraper_v1",
        "seen_invoice_ids": ["in_001"],
        "last_scan_at": "2026-04-02T00:00:00Z",
    })

    result = await scraper.scan(_make_account(), last_uid=prior)

    assert [e.uid for e in result] == ["cursor:in_002"]
    state = json.loads(scraper._last_scan_state or "{}")
    assert set(state["seen_invoice_ids"]) == {"in_001", "in_002"}


async def test_scan_navigates_billing_url_and_uses_request_context() -> None:
    links = [_make_locator(href="https://pay.stripe.com/invoice/in_001/pdf")]
    page = _make_page(
        invoice_links=links,
        pdf_bytes_by_url={"https://pay.stripe.com/invoice/in_001/pdf": b"p"},
    )
    scraper = CursorScraper(session_cls=_FakeSession(page))

    await scraper.scan(_make_account())

    page.goto.assert_awaited_once()
    args, kwargs = page.goto.call_args
    assert args[0] == CURSOR_BILLING_URL
    assert kwargs.get("wait_until") == "load"
    assert kwargs.get("timeout") == 90_000
    page.context.request.get.assert_awaited_once()
    get_args, get_kwargs = page.context.request.get.call_args
    assert get_args[0] == "https://pay.stripe.com/invoice/in_001/pdf"
    assert get_kwargs.get("headers", {}).get("Accept") == "application/pdf"


async def test_pdf_download_populates_raw_attachment_payload() -> None:
    links = [_make_locator(href="https://pay.stripe.com/invoice/in_001/pdf")]
    page = _make_page(
        invoice_links=links,
        pdf_bytes_by_url={"https://pay.stripe.com/invoice/in_001/pdf": b"%PDF-1.7 cursor"},
    )
    scraper = CursorScraper(session_cls=_FakeSession(page))

    result = await scraper.scan(_make_account())

    assert len(result) == 1
    att = result[0].attachments[0]
    assert att.payload == b"%PDF-1.7 cursor"
    assert att.content_type == "application/pdf"
    assert att.size == len(b"%PDF-1.7 cursor")
    assert att.filename == "cursor-invoice-in_001.pdf"


async def test_storage_state_is_persisted_back_to_account_via_scheduler_hook() -> None:
    links = [_make_locator(href="https://pay.stripe.com/invoice/in_001/pdf")]
    page = _make_page(
        invoice_links=links,
        pdf_bytes_by_url={"https://pay.stripe.com/invoice/in_001/pdf": b"p"},
        storage_state_payload={"cookies": [{"name": "refreshed"}], "origins": []},
    )
    account = _make_account(storage_state=json.dumps({"cookies": []}))
    scraper = CursorScraper(session_cls=_FakeSession(page))

    await scraper.scan(account)

    assert scraper._updated_storage_state is not None
    parsed = json.loads(scraper._updated_storage_state)
    assert parsed["cookies"] == [{"name": "refreshed"}]
    account.playwright_storage_state = scraper._updated_storage_state
    assert json.loads(account.playwright_storage_state)["cookies"][0]["name"] == "refreshed"


async def test_returns_empty_when_login_redirect() -> None:
    page = _make_page(url_after_goto="https://cursor.com/login?next=/dashboard")
    scraper = CursorScraper(session_cls=_FakeSession(page))

    result = await scraper.scan(_make_account())

    assert result == []
    assert scraper._last_scan_state is None
    assert any(
        event["kind"] == "auth_required" and "[CURSOR_AUTH]" in event["error_detail"]
        for event in scraper._scan_events
    )
    page.context.request.get.assert_not_called()


async def test_emits_auth_required_when_session_expired() -> None:
    page = _make_page(url_after_goto="https://cursor.com/login")
    scraper = CursorScraper(session_cls=_FakeSession(page))

    result = await scraper.scan(_make_account(storage_state=json.dumps({"cookies": []})))

    assert result == []
    assert any(
        "session cookies missing or expired" in event["error_detail"]
        for event in scraper._scan_events
    )


async def test_no_invoices_returns_empty_without_auth_required() -> None:
    page = _make_page(invoice_links=[])
    scraper = CursorScraper(session_cls=_FakeSession(page))

    result = await scraper.scan(_make_account())

    assert result == []
    assert scraper._scan_events == []
    state = json.loads(scraper._last_scan_state or "{}")
    assert state["seen_invoice_ids"] == []


async def test_pdf_fetch_http_error_skips_invoice() -> None:
    links = [
        _make_locator(href="https://pay.stripe.com/invoice/in_001/pdf"),
        _make_locator(href="https://pay.stripe.com/invoice/in_002/pdf"),
    ]
    call_count = {"n": 0}

    async def _request_get(url: str, **_kwargs: Any) -> MagicMock:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _make_response(b"", status=403)
        return _make_response(b"second-pdf-bytes")

    page = _make_page(invoice_links=links, request_get_side_effect=_request_get)
    scraper = CursorScraper(session_cls=_FakeSession(page))

    result = await scraper.scan(_make_account())

    assert [e.uid for e in result] == ["cursor:in_002"]
    assert result[0].attachments[0].payload == b"second-pdf-bytes"


async def test_caps_runtime_at_60s(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cursor_mod, "SCRAPE_TIMEOUT_SECONDS", 0.05)

    async def _hang(url: str, **_kwargs: Any) -> None:
        await asyncio.sleep(5.0)

    page = _make_page(goto_side_effect=_hang)
    scraper = CursorScraper(session_cls=_FakeSession(page))

    result = await scraper.scan(_make_account())

    assert result == []
    assert any(
        event["kind"] == "auth_required" and "[CURSOR_TIMEOUT]" in event["error_detail"]
        for event in scraper._scan_events
    )


async def test_handles_unexpected_exception_gracefully() -> None:
    page = _make_page(goto_side_effect=RuntimeError("kaboom"))
    scraper = CursorScraper(session_cls=_FakeSession(page))

    result = await scraper.scan(_make_account())

    assert result == []
    assert any(
        event["kind"] == "auth_required"
        and "[CURSOR_ERROR] RuntimeError" in event["error_detail"]
        and "kaboom" in event["error_detail"]
        for event in scraper._scan_events
    )


async def test_seen_invoice_ids_are_capped_with_fifo_eviction() -> None:
    prior_ids = [f"legacy-{i:04d}" for i in range(SEEN_INVOICE_IDS_CAP)]
    prior = json.dumps({
        "_format": "cursor_scraper_v1",
        "seen_invoice_ids": prior_ids,
        "last_scan_at": "2026-04-02T00:00:00Z",
    })
    new_links = [
        _make_locator(href=f"https://pay.stripe.com/invoice/in_fresh{i}/pdf")
        for i in range(5)
    ]
    page = _make_page(
        invoice_links=new_links,
        pdf_bytes_by_url={
            f"https://pay.stripe.com/invoice/in_fresh{i}/pdf": b"p" for i in range(5)
        },
    )
    scraper = CursorScraper(session_cls=_FakeSession(page))

    await scraper.scan(_make_account(), last_uid=prior)

    state = json.loads(scraper._last_scan_state or "{}")
    seen = state["seen_invoice_ids"]
    assert len(seen) == SEEN_INVOICE_IDS_CAP
    assert "legacy-0000" not in seen
    assert "in_fresh4" in seen
    assert "legacy-0999" in seen


async def test_link_without_href_is_skipped() -> None:
    links = [
        _make_locator(href=None),
        _make_locator(href="https://pay.stripe.com/invoice/in_002/pdf"),
    ]
    page = _make_page(
        invoice_links=links,
        pdf_bytes_by_url={"https://pay.stripe.com/invoice/in_002/pdf": b"p"},
    )
    scraper = CursorScraper(session_cls=_FakeSession(page))

    result = await scraper.scan(_make_account())

    assert [e.uid for e in result] == ["cursor:in_002"]


async def test_relative_href_is_absolutized_to_cursor_origin() -> None:
    links = [_make_locator(href="/api/billing/invoices/abc123/pdf")]
    captured: list[str] = []

    async def _request_get(url: str, **_kwargs: Any) -> MagicMock:
        captured.append(url)
        return _make_response(b"p")

    page = _make_page(invoice_links=links, request_get_side_effect=_request_get)
    scraper = CursorScraper(session_cls=_FakeSession(page))

    result = await scraper.scan(_make_account())

    assert captured == ["https://cursor.com/api/billing/invoices/abc123/pdf"]
    assert result[0].uid == "cursor:abc123"


async def test_cursor_scraper_returns_empty_when_playwright_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cursor_mod, "PLAYWRIGHT_AVAILABLE", False, raising=False)
    page = _make_page()
    scraper = CursorScraper(session_cls=_FakeSession(page))

    result = await scraper.scan(_make_account())

    assert result == []
    assert any(
        event["kind"] == "auth_required" and "[CURSOR_DEPENDENCY]" in event["error_detail"]
        for event in scraper._scan_events
    )
    assert scraper._last_scan_state is None


async def test_cursor_scraper_catches_playwright_unavailable_from_session() -> None:
    from app.services.scrapers.playwright_session import PlaywrightUnavailableError

    page = _make_page()
    session = _FakeSession(page, on_enter_exc=PlaywrightUnavailableError("no playwright"))
    scraper = CursorScraper(session_cls=session)

    result = await scraper.scan(_make_account())

    assert result == []
    assert any(
        "[CURSOR_DEPENDENCY]" in event["error_detail"] for event in scraper._scan_events
    )


async def test_scheduler_drain_converts_auth_required_events_to_extraction_logs(
    settings: Any,
    db: Any,
    create_email_account: Callable[..., Any],
    mock_ai_service: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collections.abc import AsyncIterator
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.models import ExtractionLog, ScanLog
    import app.tasks.scheduler as scheduler_mod
    from app.services.scrapers.factory import ScraperFactory

    class _StubScraperForDrain:
        def __init__(self) -> None:
            self._scan_events = [{
                "kind": "auth_required",
                "error_detail": "[CURSOR_AUTH] no creds stored (drain test)",
            }]
            self._last_scan_state = None
            self._updated_storage_state = None

        async def scan(
            self, account: Any, last_uid: Any = None, options: Any = None,
            progress_callback: Any = None,
        ) -> list[Any]:
            del account, last_uid, options, progress_callback
            return []

    session_factory = async_sessionmaker(bind=db.bind, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(scheduler_mod, "get_db", override_get_db)
    monkeypatch.setattr(scheduler_mod, "AIService", lambda s: mock_ai_service)
    monkeypatch.setattr(
        ScraperFactory, "get_scraper", staticmethod(lambda t: _StubScraperForDrain())
    )
    monkeypatch.setattr(
        ScraperFactory, "is_scraper_type", staticmethod(lambda t: t == "cursor")
    )

    await create_email_account(type="cursor", host=None, port=None)
    await scheduler_mod.scan_all_accounts()

    logs = (await db.execute(select(ExtractionLog))).scalars().all()
    assert any(
        log.outcome == "auth_required"
        and log.error_detail is not None
        and "CURSOR_AUTH" in log.error_detail
        for log in logs
    ), [log.outcome for log in logs]
    assert (await db.execute(select(ScanLog))).scalars().first() is not None


async def test_scheduler_persists_updated_storage_state_when_scraper_sets_it(
    db: Any,
    create_email_account: Callable[..., Any],
    mock_ai_service: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collections.abc import AsyncIterator
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.models import EmailAccount
    import app.tasks.scheduler as scheduler_mod
    from app.services.scrapers.factory import ScraperFactory

    class _StubScraperEmittingState:
        def __init__(self) -> None:
            self._scan_events: list[dict[str, Any]] = []
            self._last_scan_state = None
            self._updated_storage_state = '{"cookies": [{"name": "rotated"}]}'

        async def scan(
            self, account: Any, last_uid: Any = None, options: Any = None,
            progress_callback: Any = None,
        ) -> list[Any]:
            del account, last_uid, options, progress_callback
            return []

    session_factory = async_sessionmaker(bind=db.bind, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(scheduler_mod, "get_db", override_get_db)
    monkeypatch.setattr(scheduler_mod, "AIService", lambda s: mock_ai_service)
    monkeypatch.setattr(
        ScraperFactory, "get_scraper", staticmethod(lambda t: _StubScraperEmittingState())
    )
    monkeypatch.setattr(
        ScraperFactory, "is_scraper_type", staticmethod(lambda t: t == "cursor")
    )

    account = await create_email_account(type="cursor", host=None, port=None)
    await scheduler_mod.scan_all_accounts()

    refreshed = (await db.execute(select(EmailAccount).where(EmailAccount.id == account.id))).scalar_one()
    await db.refresh(refreshed)
    assert refreshed.playwright_storage_state == '{"cookies": [{"name": "rotated"}]}'


def test_scrape_timeout_constant_is_60_seconds() -> None:
    assert SCRAPE_TIMEOUT_SECONDS == 60.0


async def test_protocol_relative_href_is_absolutized() -> None:
    links = [_make_locator(href="//pay.stripe.com/inv/in_xyz/pdf")]
    captured: list[str] = []

    async def _request_get(url: str, **_kwargs: Any) -> MagicMock:
        captured.append(url)
        return _make_response(b"p")

    page = _make_page(invoice_links=links, request_get_side_effect=_request_get)
    scraper = CursorScraper(session_cls=_FakeSession(page))

    result = await scraper.scan(_make_account())

    assert captured == ["https://pay.stripe.com/inv/in_xyz/pdf"]
    assert [e.uid for e in result] == ["cursor:in_xyz"]


async def test_unknown_url_pattern_falls_back_to_tail_segment() -> None:
    links = [_make_locator(href="https://example.com/billing/weirdo-42/pdf")]
    page = _make_page(
        invoice_links=links,
        pdf_bytes_by_url={"https://example.com/billing/weirdo-42/pdf": b"p"},
    )
    scraper = CursorScraper(session_cls=_FakeSession(page))

    result = await scraper.scan(_make_account())

    assert [e.uid for e in result] == ["cursor:weirdo-42"]


async def test_url_without_pdf_suffix_falls_back_to_full_tail() -> None:
    links = [_make_locator(href="https://example.com/billing/weirdo-99")]
    page = _make_page(
        invoice_links=links,
        pdf_bytes_by_url={"https://example.com/billing/weirdo-99": b"p"},
    )
    scraper = CursorScraper(session_cls=_FakeSession(page))

    result = await scraper.scan(_make_account())

    assert [e.uid for e in result] == ["cursor:weirdo-99"]


async def test_wait_for_selector_timeout_falls_through_to_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    links = [_make_locator(href="https://pay.stripe.com/invoice/in_001/pdf")]
    page = _make_page(
        invoice_links=links,
        pdf_bytes_by_url={"https://pay.stripe.com/invoice/in_001/pdf": b"p"},
        wait_for_selector_side_effect=cursor_mod.PlaywrightTimeoutError("waited too long"),
    )
    scraper = CursorScraper(session_cls=_FakeSession(page))

    result = await scraper.scan(_make_account())

    assert [e.uid for e in result] == ["cursor:in_001"]


async def test_pdf_request_timeout_skips_invoice() -> None:
    links = [
        _make_locator(href="https://pay.stripe.com/invoice/in_001/pdf"),
        _make_locator(href="https://pay.stripe.com/invoice/in_002/pdf"),
    ]
    call_count = {"n": 0}

    async def _request_get(url: str, **_kwargs: Any) -> MagicMock:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise cursor_mod.PlaywrightTimeoutError("network timeout")
        return _make_response(b"second")

    page = _make_page(invoice_links=links, request_get_side_effect=_request_get)
    scraper = CursorScraper(session_cls=_FakeSession(page))

    result = await scraper.scan(_make_account())

    assert [e.uid for e in result] == ["cursor:in_002"]


async def test_pdf_response_with_callable_status_is_handled() -> None:
    links = [_make_locator(href="https://pay.stripe.com/invoice/in_001/pdf")]

    async def _request_get(url: str, **_kwargs: Any) -> MagicMock:
        resp = MagicMock(name="response-callable-status")
        resp.status = MagicMock(return_value=503)
        resp.body = AsyncMock(return_value=b"")
        return resp

    page = _make_page(invoice_links=links, request_get_side_effect=_request_get)
    scraper = CursorScraper(session_cls=_FakeSession(page))

    result = await scraper.scan(_make_account())

    assert result == []


async def test_pdf_response_with_empty_body_skips_invoice() -> None:
    links = [_make_locator(href="https://pay.stripe.com/invoice/in_001/pdf")]
    page = _make_page(
        invoice_links=links,
        pdf_bytes_by_url={"https://pay.stripe.com/invoice/in_001/pdf": b""},
    )
    scraper = CursorScraper(session_cls=_FakeSession(page))

    result = await scraper.scan(_make_account())

    assert result == []


async def test_row_meta_with_empty_text_falls_back_to_default_subject() -> None:
    link = _make_locator(href="https://pay.stripe.com/invoice/in_001/pdf", row_text=None)
    row = link.locator.return_value
    row.is_visible = AsyncMock(return_value=True)
    row.text_content = AsyncMock(return_value="")

    page = _make_page(
        invoice_links=[link],
        pdf_bytes_by_url={"https://pay.stripe.com/invoice/in_001/pdf": b"p"},
    )
    scraper = CursorScraper(session_cls=_FakeSession(page))

    result = await scraper.scan(_make_account())

    assert result[0].subject == "Cursor Invoice in_001"


async def test_opaque_href_passes_through_unchanged() -> None:
    links = [_make_locator(href="mailto:billing@cursor.com")]

    async def _capture_get(url: str, **_kwargs: Any) -> MagicMock:
        return _make_response(b"p")

    page = _make_page(invoice_links=links, request_get_side_effect=_capture_get)
    scraper = CursorScraper(session_cls=_FakeSession(page))

    result = await scraper.scan(_make_account())

    captured_url = page.context.request.get.call_args[0][0]
    assert captured_url == "mailto:billing@cursor.com"
    assert result[0].uid == "cursor:mailto:billing@cursor.com"
