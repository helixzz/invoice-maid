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
    HTTP_PDF_TIMEOUT_MS,
    SCRAPE_TIMEOUT_SECONDS,
    SEEN_URLS_CAP,
    CursorScraper,
)


INVOICE_URL_1 = (
    "https://invoice.stripe.com/i/acct_1Lb5LzB4TZWxSIGU/"
    "live_YWNjdF8xTGI1THpCNFRaV3hTSUdVLEludm9pY2VOdW1iZXJPbmUsMTY4OTQxMjA5?s=il&"
)
INVOICE_URL_2 = (
    "https://invoice.stripe.com/i/acct_1Lb5LzB4TZWxSIGU/"
    "live_YWNjdF8xTGI1THpCNFRaV3hTSUdVLEludm9pY2VOdW1iZXJUd28sMTY4OTQxMjEw?s=il&"
)
INVOICE_URL_3 = (
    "https://invoice.stripe.com/i/acct_1Lb5LzB4TZWxSIGU/"
    "live_YWNjdF8xTGI1THpCNFRaV3hTSUdVLEludm9pY2VOdW1iZXJUaHJlZSwxNjg5NDEyMTE?s=il&"
)


def _invoice_page_text(date: str, amount: str) -> str:
    return f"Invoice\n{date}\n{amount}\nPaid\nPro Plan · Monthly"


def _portal_html(urls: list[str]) -> str:
    rows = "\n".join(
        f'<tr><td><a href="{u}">View invoice</a></td></tr>' for u in urls
    )
    return f"<html><body><table>{rows}</table></body></html>"


def _make_download(pdf_bytes: bytes, *, tmp_path: Any) -> MagicMock:
    pdf_path = tmp_path / "invoice.pdf"
    pdf_path.write_bytes(pdf_bytes)
    download = MagicMock(name="download")
    download.path = AsyncMock(return_value=str(pdf_path))
    download.suggested_filename = "Invoice-001.pdf"
    return download


def _make_expect_download_cm(download: MagicMock) -> MagicMock:
    class _Info:
        @property
        def value(self) -> Any:
            async def _resolve() -> MagicMock:
                return download
            return _resolve()

    info = _Info()
    cm = MagicMock(name="expect-download-cm")
    cm.__aenter__ = AsyncMock(return_value=info)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


def _make_expect_page_cm(new_page: MagicMock) -> MagicMock:
    class _Info:
        @property
        def value(self) -> Any:
            async def _resolve() -> MagicMock:
                return new_page
            return _resolve()

    info = _Info()
    cm = MagicMock(name="expect-page-cm")
    cm.__aenter__ = AsyncMock(return_value=info)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


_UNSET = object()


class _FakeHTTPResponse:
    def __init__(self, status: int, body: bytes, content_type: str) -> None:
        self.status = status
        self._body = body
        self.headers = {"content-type": content_type}

    async def body(self) -> bytes:
        return self._body


def _make_stripe_page(
    *,
    tmp_path: Any,
    portal_html: str = "",
    invoice_pages: dict[str, dict[str, Any]] | None = None,
    goto_side_effect: Any = None,
    has_download_button: bool = True,
    download_exc: BaseException | None = None,
    expect_download_exc: BaseException | None = None,
    download_pdf_bytes: bytes = b"%PDF-1.7\n" + b"X" * 2048,
    download_path_override: Any = _UNSET,
    http_pdf_response: Any = _UNSET,
) -> MagicMock:
    stripe_page = MagicMock(name="stripe_page")
    stripe_page.url = "https://billing.stripe.com/session/xxx"
    invoice_pages = invoice_pages or {}
    current_url: dict[str, str] = {"url": stripe_page.url}

    async def _goto(url: str, **_kwargs: Any) -> None:
        if goto_side_effect is not None:
            if isinstance(goto_side_effect, BaseException):
                raise goto_side_effect
            await goto_side_effect(url, **_kwargs)
        current_url["url"] = url
        stripe_page.url = url

    stripe_page.goto = AsyncMock(side_effect=_goto)
    stripe_page.content = AsyncMock(return_value=portal_html)

    async def _inner_text(_sel: str) -> str:
        page_meta = invoice_pages.get(current_url["url"]) or {}
        return page_meta.get("text", "")

    stripe_page.inner_text = AsyncMock(side_effect=_inner_text)
    stripe_page.text_content = AsyncMock(side_effect=_inner_text)
    stripe_page.evaluate = AsyncMock(return_value=None)
    stripe_page.wait_for_load_state = AsyncMock(return_value=None)
    stripe_page.close = AsyncMock(return_value=None)

    download_button = MagicMock(name="download-btn")
    download_button.count = AsyncMock(
        return_value=1 if has_download_button else 0
    )
    download_button.click = AsyncMock(return_value=None)

    download = _make_download(download_pdf_bytes, tmp_path=tmp_path)
    if download_path_override is not _UNSET:
        download.path = AsyncMock(return_value=download_path_override)

    if expect_download_exc is not None:
        def _expect_download(*_args: Any, **_kwargs: Any) -> Any:
            raise expect_download_exc
        stripe_page.expect_download = MagicMock(side_effect=_expect_download)
    else:
        stripe_page.expect_download = MagicMock(
            return_value=_make_expect_download_cm(download)
        )

    if download_exc is not None:
        download_button.click = AsyncMock(side_effect=download_exc)

    def _locator(selector: str) -> MagicMock:
        top = MagicMock(name=f"stripe-locator[{selector}]")
        if 'Download invoice' in selector:
            top.first = download_button
        else:
            top.first = MagicMock()
            top.first.count = AsyncMock(return_value=0)
        return top

    stripe_page.locator = MagicMock(side_effect=_locator)

    # HTTP PDF download path: stripe_page.context.request.get(url)
    http_get = AsyncMock()
    if http_pdf_response is _UNSET:
        # Default: HTTP GET fails (forces Playwright fallback)
        http_get.return_value = _FakeHTTPResponse(status=404, body=b"", content_type="text/html")
    elif isinstance(http_pdf_response, BaseException):
        http_get.side_effect = http_pdf_response
    else:
        http_get.return_value = http_pdf_response
    http_request = MagicMock(name="http-request")
    http_request.get = http_get
    stripe_context = MagicMock(name="stripe-context")
    stripe_context.request = http_request
    stripe_page.context = stripe_context

    return stripe_page


def _make_cursor_page(
    *,
    url_after_goto: str = CURSOR_BILLING_URL,
    stripe_page: MagicMock | None = None,
    has_manage_button: bool = True,
    goto_side_effect: Any = None,
    storage_state_payload: dict[str, Any] | None = None,
) -> MagicMock:
    page = MagicMock(name="cursor_page")
    page.url = url_after_goto

    async def _goto(_url: str, **_kwargs: Any) -> None:
        if goto_side_effect is not None:
            if isinstance(goto_side_effect, BaseException):
                raise goto_side_effect
            await goto_side_effect(_url, **_kwargs)
        page.url = url_after_goto

    page.goto = AsyncMock(side_effect=_goto)

    manage_button = MagicMock(name="manage-btn")
    manage_button.count = AsyncMock(
        return_value=1 if has_manage_button else 0
    )
    manage_button.click = AsyncMock(return_value=None)

    def _locator(selector: str) -> MagicMock:
        top = MagicMock(name=f"cursor-locator[{selector}]")
        if 'Manage in Stripe' in selector:
            top.first = manage_button
        else:
            top.first = MagicMock()
            top.first.count = AsyncMock(return_value=0)
        return top

    page.locator = MagicMock(side_effect=_locator)

    context = MagicMock(name="context")
    if stripe_page is not None:
        context.expect_page = MagicMock(
            return_value=_make_expect_page_cm(stripe_page)
        )
    else:
        context.expect_page = MagicMock(
            return_value=_make_expect_page_cm(MagicMock())
        )
    context.storage_state = AsyncMock(
        return_value=storage_state_payload
        or {"cookies": [{"name": "fresh"}], "origins": []}
    )
    page.context = context
    return page


class _FakeSession:
    def __init__(
        self,
        page: MagicMock,
        *,
        on_enter_exc: BaseException | None = None,
    ) -> None:
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


async def test_happy_path_three_invoices_all_downloaded(tmp_path: Any) -> None:
    stripe_page = _make_stripe_page(
        tmp_path=tmp_path,
        portal_html=_portal_html([INVOICE_URL_1, INVOICE_URL_2, INVOICE_URL_3]),
        invoice_pages={
            INVOICE_URL_1: {"text": _invoice_page_text("May 7, 2026", "$1,001.79")},
            INVOICE_URL_2: {"text": _invoice_page_text("Apr 7, 2026", "$999.00")},
            INVOICE_URL_3: {"text": _invoice_page_text("Mar 7, 2026", "$42.00")},
        },
    )
    cursor_page = _make_cursor_page(stripe_page=stripe_page)
    scraper = CursorScraper(session_cls=_FakeSession(cursor_page))
    progress: list[dict[str, Any]] = []

    result = await scraper.scan(
        _make_account(storage_state=json.dumps({"cookies": []})),
        last_uid=None,
        progress_callback=progress.append,
    )

    assert len(result) == 3
    assert result[0].from_addr == "billing@cursor.com"
    assert result[0].attachments[0].content_type == "application/pdf"
    assert result[0].attachments[0].payload.startswith(b"%PDF-1.7")
    assert "May 7, 2026" in result[0].subject
    assert "$1,001.79" in result[0].subject
    state = json.loads(scraper._last_scan_state or "{}")
    assert state["_format"] == "cursor_stripe_v1"
    assert state["seen_urls"] == [INVOICE_URL_1, INVOICE_URL_2, INVOICE_URL_3]
    assert len(progress) == 3
    cursor_page.goto.assert_awaited_once()
    args, kwargs = cursor_page.goto.call_args
    assert args[0] == CURSOR_BILLING_URL
    assert kwargs.get("timeout") == 90_000


async def test_no_invoices_returns_empty_without_auth_required(tmp_path: Any) -> None:
    stripe_page = _make_stripe_page(tmp_path=tmp_path, portal_html="<html></html>")
    cursor_page = _make_cursor_page(stripe_page=stripe_page)
    scraper = CursorScraper(session_cls=_FakeSession(cursor_page))

    result = await scraper.scan(_make_account())

    assert result == []
    assert scraper._scan_events == []
    state = json.loads(scraper._last_scan_state or "{}")
    assert state["seen_urls"] == []


async def test_login_redirect_emits_auth_required(tmp_path: Any) -> None:
    del tmp_path
    cursor_page = _make_cursor_page(url_after_goto="https://cursor.com/login?next=/dashboard")
    scraper = CursorScraper(session_cls=_FakeSession(cursor_page))

    result = await scraper.scan(_make_account())

    assert result == []
    assert scraper._last_scan_state is None
    assert any(
        event["kind"] == "auth_required" and "[CURSOR_AUTH]" in event["error_detail"]
        for event in scraper._scan_events
    )
    cursor_page.context.expect_page.assert_not_called()


async def test_missing_manage_in_stripe_button_returns_empty(tmp_path: Any) -> None:
    del tmp_path
    cursor_page = _make_cursor_page(has_manage_button=False)
    scraper = CursorScraper(session_cls=_FakeSession(cursor_page))

    result = await scraper.scan(_make_account())

    assert result == []
    assert scraper._scan_events == []
    state = json.loads(scraper._last_scan_state or "{}")
    assert state["seen_urls"] == []


async def test_invoice_url_regex_extracts_from_raw_html(tmp_path: Any) -> None:
    html = (
        "<html><body>"
        f"<a href='{INVOICE_URL_1}'>Invoice 1</a>"
        f"<div>{INVOICE_URL_1}</div>"
        f"<span data-url=\"{INVOICE_URL_2}\"></span>"
        "<p>Not-a-url</p>"
        "</body></html>"
    )
    stripe_page = _make_stripe_page(
        tmp_path=tmp_path,
        portal_html=html,
        invoice_pages={
            INVOICE_URL_1: {"text": _invoice_page_text("May 7, 2026", "$10.00")},
            INVOICE_URL_2: {"text": _invoice_page_text("Apr 7, 2026", "$20.00")},
        },
    )
    cursor_page = _make_cursor_page(stripe_page=stripe_page)
    scraper = CursorScraper(session_cls=_FakeSession(cursor_page))

    result = await scraper.scan(_make_account())

    assert len(result) == 2
    state = json.loads(scraper._last_scan_state or "{}")
    assert state["seen_urls"] == [INVOICE_URL_1, INVOICE_URL_2]


async def test_metadata_extraction_from_invoice_page_text(tmp_path: Any) -> None:
    stripe_page = _make_stripe_page(
        tmp_path=tmp_path,
        portal_html=_portal_html([INVOICE_URL_1]),
        invoice_pages={
            INVOICE_URL_1: {"text": "Invoice\nMay 7, 2026\n$1,001.79\nPaid"},
        },
    )
    cursor_page = _make_cursor_page(stripe_page=stripe_page)
    scraper = CursorScraper(session_cls=_FakeSession(cursor_page))

    result = await scraper.scan(_make_account())

    assert len(result) == 1
    assert "May 7, 2026" in result[0].body_text
    assert "$1,001.79" in result[0].body_text
    assert "May 7, 2026" in result[0].subject


async def test_metadata_extraction_missing_fields_leaves_defaults() -> None:
    scraper = CursorScraper()

    empty = scraper._extract_invoice_metadata("")
    assert empty == {"date_text": "", "amount_text": "", "page_text": ""}

    no_money = scraper._extract_invoice_metadata("Invoice\nMay 7, 2026\nPaid")
    assert no_money["date_text"] == "May 7, 2026"
    assert no_money["amount_text"] == ""

    no_date = scraper._extract_invoice_metadata("Invoice\n$10.00\nPaid")
    assert no_date["amount_text"] == "$10.00"
    assert no_date["date_text"] == ""


async def test_download_failure_skips_single_invoice(tmp_path: Any) -> None:
    stripe_page = _make_stripe_page(
        tmp_path=tmp_path,
        portal_html=_portal_html([INVOICE_URL_1, INVOICE_URL_2]),
        invoice_pages={
            INVOICE_URL_1: {"text": _invoice_page_text("May 7, 2026", "$10.00")},
            INVOICE_URL_2: {"text": _invoice_page_text("Apr 7, 2026", "$20.00")},
        },
    )
    real_expect_download = stripe_page.expect_download
    call_count = {"n": 0}

    def _flaky_expect_download(*args: Any, **kwargs: Any) -> Any:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise cursor_mod.PlaywrightTimeoutError("download timed out")
        return real_expect_download(*args, **kwargs)

    stripe_page.expect_download = MagicMock(side_effect=_flaky_expect_download)
    cursor_page = _make_cursor_page(stripe_page=stripe_page)
    scraper = CursorScraper(session_cls=_FakeSession(cursor_page))

    result = await scraper.scan(_make_account())

    assert len(result) == 1
    state = json.loads(scraper._last_scan_state or "{}")
    assert state["seen_urls"] == [INVOICE_URL_2]


async def test_download_generic_exception_skips_invoice(tmp_path: Any) -> None:
    stripe_page = _make_stripe_page(
        tmp_path=tmp_path,
        portal_html=_portal_html([INVOICE_URL_1]),
        invoice_pages={
            INVOICE_URL_1: {"text": _invoice_page_text("May 7, 2026", "$10.00")},
        },
        expect_download_exc=RuntimeError("protocol error"),
    )
    cursor_page = _make_cursor_page(stripe_page=stripe_page)
    scraper = CursorScraper(session_cls=_FakeSession(cursor_page))

    result = await scraper.scan(_make_account())

    assert result == []
    state = json.loads(scraper._last_scan_state or "{}")
    assert state["seen_urls"] == []


async def test_seen_url_dedup_skips_previously_seen(tmp_path: Any) -> None:
    stripe_page = _make_stripe_page(
        tmp_path=tmp_path,
        portal_html=_portal_html([INVOICE_URL_1, INVOICE_URL_2]),
        invoice_pages={
            INVOICE_URL_1: {"text": _invoice_page_text("May 7, 2026", "$10.00")},
            INVOICE_URL_2: {"text": _invoice_page_text("Apr 7, 2026", "$20.00")},
        },
    )
    cursor_page = _make_cursor_page(stripe_page=stripe_page)
    scraper = CursorScraper(session_cls=_FakeSession(cursor_page))
    prior_state = json.dumps({
        "_format": "cursor_stripe_v1",
        "seen_urls": [INVOICE_URL_1],
        "last_scan_at": "2026-04-02T00:00:00Z",
    })

    result = await scraper.scan(_make_account(), last_uid=prior_state)

    assert len(result) == 1
    state = json.loads(scraper._last_scan_state or "{}")
    assert set(state["seen_urls"]) == {INVOICE_URL_1, INVOICE_URL_2}


async def test_seen_url_dedup_second_visit_returns_zero_new(tmp_path: Any) -> None:
    stripe_page = _make_stripe_page(
        tmp_path=tmp_path,
        portal_html=_portal_html([INVOICE_URL_1, INVOICE_URL_2]),
        invoice_pages={
            INVOICE_URL_1: {"text": _invoice_page_text("May 7, 2026", "$10.00")},
            INVOICE_URL_2: {"text": _invoice_page_text("Apr 7, 2026", "$20.00")},
        },
    )
    cursor_page = _make_cursor_page(stripe_page=stripe_page)
    scraper = CursorScraper(session_cls=_FakeSession(cursor_page))
    already_seen = json.dumps({
        "_format": "cursor_stripe_v1",
        "seen_urls": [INVOICE_URL_1, INVOICE_URL_2],
        "last_scan_at": "2026-04-02T00:00:00Z",
    })

    result = await scraper.scan(_make_account(), last_uid=already_seen)

    assert result == []
    state = json.loads(scraper._last_scan_state or "{}")
    assert set(state["seen_urls"]) == {INVOICE_URL_1, INVOICE_URL_2}


async def test_unexpected_exception_caught_and_emits_auth_required(tmp_path: Any) -> None:
    del tmp_path
    cursor_page = _make_cursor_page(goto_side_effect=RuntimeError("kaboom"))
    scraper = CursorScraper(session_cls=_FakeSession(cursor_page))

    result = await scraper.scan(_make_account())

    assert result == []
    assert any(
        event["kind"] == "auth_required"
        and "[CURSOR_ERROR] RuntimeError" in event["error_detail"]
        and "kaboom" in event["error_detail"]
        for event in scraper._scan_events
    )


async def test_sixty_second_timeout_cap(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    del tmp_path
    monkeypatch.setattr(cursor_mod, "SCRAPE_TIMEOUT_SECONDS", 0.05)

    async def _hang(_url: str, **_kwargs: Any) -> None:
        await asyncio.sleep(5.0)

    cursor_page = _make_cursor_page(goto_side_effect=_hang)
    scraper = CursorScraper(session_cls=_FakeSession(cursor_page))

    result = await scraper.scan(_make_account())

    assert result == []
    assert any(
        event["kind"] == "auth_required" and "[CURSOR_TIMEOUT]" in event["error_detail"]
        for event in scraper._scan_events
    )


async def test_scrape_timeout_constant_is_900_seconds() -> None:
    assert SCRAPE_TIMEOUT_SECONDS == 900.0
    assert HTTP_PDF_TIMEOUT_MS == 15_000


async def test_download_via_http_success(tmp_path: Any) -> None:
    """HTTP GET {url}/pdf returns valid PDF — no Playwright fallback needed."""
    pdf_body = b"%PDF-1.7\ngood"
    stripe_page = _make_stripe_page(
        tmp_path=tmp_path,
        http_pdf_response=_FakeHTTPResponse(status=200, body=pdf_body, content_type="application/pdf"),
    )
    scraper = CursorScraper(session_cls=_FakeSession(_make_cursor_page(stripe_page=stripe_page)))
    result = await scraper._download_via_http(stripe_page, INVOICE_URL_1)
    assert result == pdf_body


async def test_download_via_http_non_200(tmp_path: Any) -> None:
    stripe_page = _make_stripe_page(
        tmp_path=tmp_path,
        http_pdf_response=_FakeHTTPResponse(status=404, body=b"not found", content_type="text/html"),
    )
    scraper = CursorScraper(session_cls=_FakeSession(_make_cursor_page(stripe_page=stripe_page)))
    result = await scraper._download_via_http(stripe_page, INVOICE_URL_1)
    assert result is None


async def test_download_via_http_non_pdf_content_type(tmp_path: Any) -> None:
    stripe_page = _make_stripe_page(
        tmp_path=tmp_path,
        http_pdf_response=_FakeHTTPResponse(status=200, body=b"%PDF-1.7\nx", content_type="text/html"),
    )
    scraper = CursorScraper(session_cls=_FakeSession(_make_cursor_page(stripe_page=stripe_page)))
    result = await scraper._download_via_http(stripe_page, INVOICE_URL_1)
    assert result is None


async def test_download_via_http_invalid_pdf_body(tmp_path: Any) -> None:
    stripe_page = _make_stripe_page(
        tmp_path=tmp_path,
        http_pdf_response=_FakeHTTPResponse(status=200, body=b"<html>...</html>", content_type="application/pdf"),
    )
    scraper = CursorScraper(session_cls=_FakeSession(_make_cursor_page(stripe_page=stripe_page)))
    result = await scraper._download_via_http(stripe_page, INVOICE_URL_1)
    assert result is None


async def test_download_via_http_exception_falls_back(tmp_path: Any) -> None:
    """HTTP GET raises → falls back to Playwright expect_download."""
    pdf_body = b"%PDF-1.7\nfallback"
    stripe_page = _make_stripe_page(
        tmp_path=tmp_path,
        http_pdf_response=Exception("Connection reset"),
        download_pdf_bytes=pdf_body,
    )
    scraper = CursorScraper(session_cls=_FakeSession(_make_cursor_page(stripe_page=stripe_page)))
    result = await scraper._download_invoice_pdf(stripe_page, INVOICE_URL_1)
    assert result == pdf_body


async def test_download_invoice_pdf_http_then_fallback(tmp_path: Any) -> None:
    """HTTP GET fails (404) → falls back to Playwright button click."""
    pdf_body = b"%PDF-1.7\nboth"
    stripe_page = _make_stripe_page(
        tmp_path=tmp_path,
        http_pdf_response=_FakeHTTPResponse(status=404, body=b"", content_type="text/html"),
        download_pdf_bytes=pdf_body,
    )
    scraper = CursorScraper(session_cls=_FakeSession(_make_cursor_page(stripe_page=stripe_page)))
    result = await scraper._download_invoice_pdf(stripe_page, INVOICE_URL_1)
    assert result == pdf_body


async def test_storage_state_captured_back_to_scheduler_hook(tmp_path: Any) -> None:
    stripe_page = _make_stripe_page(
        tmp_path=tmp_path,
        portal_html=_portal_html([INVOICE_URL_1]),
        invoice_pages={
            INVOICE_URL_1: {"text": _invoice_page_text("May 7, 2026", "$10.00")},
        },
    )
    cursor_page = _make_cursor_page(
        stripe_page=stripe_page,
        storage_state_payload={"cookies": [{"name": "refreshed"}], "origins": []},
    )
    account = _make_account(storage_state=json.dumps({"cookies": []}))
    scraper = CursorScraper(session_cls=_FakeSession(cursor_page))

    await scraper.scan(account)

    assert scraper._updated_storage_state is not None
    parsed = json.loads(scraper._updated_storage_state)
    assert parsed["cookies"] == [{"name": "refreshed"}]


async def test_playwright_not_installed_emits_auth_required(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    del tmp_path
    monkeypatch.setattr(cursor_mod, "PLAYWRIGHT_AVAILABLE", False, raising=False)
    cursor_page = _make_cursor_page()
    scraper = CursorScraper(session_cls=_FakeSession(cursor_page))

    result = await scraper.scan(_make_account())

    assert result == []
    assert any(
        event["kind"] == "auth_required" and "[CURSOR_DEPENDENCY]" in event["error_detail"]
        for event in scraper._scan_events
    )
    assert scraper._last_scan_state is None


async def test_playwright_unavailable_from_session_caught() -> None:
    from app.services.scrapers.playwright_session import PlaywrightUnavailableError

    cursor_page = _make_cursor_page()
    session = _FakeSession(cursor_page, on_enter_exc=PlaywrightUnavailableError("no playwright"))
    scraper = CursorScraper(session_cls=session)

    result = await scraper.scan(_make_account())

    assert result == []
    assert any(
        "[CURSOR_DEPENDENCY]" in event["error_detail"] for event in scraper._scan_events
    )


async def test_default_session_cls_is_playwright_session() -> None:
    scraper = CursorScraper()
    from app.services.scrapers.playwright_session import PlaywrightSession

    assert scraper._session_cls is PlaywrightSession


async def test_invoice_page_navigation_timeout_skips_invoice(tmp_path: Any) -> None:
    stripe_page = _make_stripe_page(
        tmp_path=tmp_path,
        portal_html=_portal_html([INVOICE_URL_1, INVOICE_URL_2]),
        invoice_pages={
            INVOICE_URL_2: {"text": _invoice_page_text("May 7, 2026", "$20.00")},
        },
    )
    call_count = {"n": 0}
    original_goto = stripe_page.goto.side_effect

    async def _flaky_goto(url: str, **kwargs: Any) -> None:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise cursor_mod.PlaywrightTimeoutError("navigation timed out")
        if original_goto is not None:
            await original_goto(url, **kwargs)
        stripe_page.url = url

    stripe_page.goto = AsyncMock(side_effect=_flaky_goto)
    cursor_page = _make_cursor_page(stripe_page=stripe_page)
    scraper = CursorScraper(session_cls=_FakeSession(cursor_page))

    result = await scraper.scan(_make_account())

    assert len(result) == 1
    state = json.loads(scraper._last_scan_state or "{}")
    assert state["seen_urls"] == [INVOICE_URL_2]


async def test_download_button_missing_skips_invoice(tmp_path: Any) -> None:
    stripe_page = _make_stripe_page(
        tmp_path=tmp_path,
        portal_html=_portal_html([INVOICE_URL_1]),
        invoice_pages={
            INVOICE_URL_1: {"text": _invoice_page_text("May 7, 2026", "$10.00")},
        },
        has_download_button=False,
    )
    cursor_page = _make_cursor_page(stripe_page=stripe_page)
    scraper = CursorScraper(session_cls=_FakeSession(cursor_page))

    result = await scraper.scan(_make_account())

    assert result == []


async def test_download_path_is_none_skips_invoice(tmp_path: Any) -> None:
    stripe_page = _make_stripe_page(
        tmp_path=tmp_path,
        portal_html=_portal_html([INVOICE_URL_1]),
        invoice_pages={
            INVOICE_URL_1: {"text": _invoice_page_text("May 7, 2026", "$10.00")},
        },
        download_path_override=None,
    )
    cursor_page = _make_cursor_page(stripe_page=stripe_page)
    scraper = CursorScraper(session_cls=_FakeSession(cursor_page))

    result = await scraper.scan(_make_account())

    assert result == []


async def test_download_path_read_failure_skips_invoice(tmp_path: Any) -> None:
    stripe_page = _make_stripe_page(
        tmp_path=tmp_path,
        portal_html=_portal_html([INVOICE_URL_1]),
        invoice_pages={
            INVOICE_URL_1: {"text": _invoice_page_text("May 7, 2026", "$10.00")},
        },
        download_path_override="/this/path/does/not/exist/invoice.pdf",
    )
    cursor_page = _make_cursor_page(stripe_page=stripe_page)
    scraper = CursorScraper(session_cls=_FakeSession(cursor_page))

    result = await scraper.scan(_make_account())

    assert result == []


async def test_empty_pdf_bytes_skips_invoice(tmp_path: Any) -> None:
    stripe_page = _make_stripe_page(
        tmp_path=tmp_path,
        portal_html=_portal_html([INVOICE_URL_1]),
        invoice_pages={
            INVOICE_URL_1: {"text": _invoice_page_text("May 7, 2026", "$10.00")},
        },
        download_pdf_bytes=b"",
    )
    cursor_page = _make_cursor_page(stripe_page=stripe_page)
    scraper = CursorScraper(session_cls=_FakeSession(cursor_page))

    result = await scraper.scan(_make_account())

    assert result == []


async def test_empty_portal_html_returns_empty(tmp_path: Any) -> None:
    stripe_page = _make_stripe_page(tmp_path=tmp_path, portal_html="")
    cursor_page = _make_cursor_page(stripe_page=stripe_page)
    scraper = CursorScraper(session_cls=_FakeSession(cursor_page))

    result = await scraper.scan(_make_account())

    assert result == []


async def test_inner_text_fallback_to_text_content(tmp_path: Any) -> None:
    stripe_page = _make_stripe_page(
        tmp_path=tmp_path,
        portal_html=_portal_html([INVOICE_URL_1]),
        invoice_pages={
            INVOICE_URL_1: {"text": _invoice_page_text("May 7, 2026", "$10.00")},
        },
    )
    stripe_page.inner_text = AsyncMock(side_effect=RuntimeError("strict csp blocked"))
    cursor_page = _make_cursor_page(stripe_page=stripe_page)
    scraper = CursorScraper(session_cls=_FakeSession(cursor_page))

    result = await scraper.scan(_make_account())

    assert len(result) == 1
    assert "May 7, 2026" in result[0].body_text


async def test_inner_text_none_returns_empty_string(tmp_path: Any) -> None:
    stripe_page = _make_stripe_page(
        tmp_path=tmp_path,
        portal_html=_portal_html([INVOICE_URL_1]),
        invoice_pages={
            INVOICE_URL_1: {"text": ""},
        },
    )
    stripe_page.inner_text = AsyncMock(return_value=None)
    cursor_page = _make_cursor_page(stripe_page=stripe_page)
    scraper = CursorScraper(session_cls=_FakeSession(cursor_page))

    result = await scraper.scan(_make_account())

    assert len(result) == 1
    assert result[0].subject == f"Cursor Invoice {_invoice_id_prefix(INVOICE_URL_1)}"


def _invoice_id_prefix(url: str) -> str:
    from app.services.scrapers.cursor import _invoice_id_from_url

    return _invoice_id_from_url(url)


async def test_invoice_id_from_url_fallback_shapes() -> None:
    from app.services.scrapers.cursor import _invoice_id_from_url

    std = _invoice_id_from_url(INVOICE_URL_1)
    assert std.startswith("live_")

    weird = _invoice_id_from_url("https://invoice.stripe.com/i/acct_x/")
    assert weird == "acct_x"

    wilder = _invoice_id_from_url("https://billing.example.com/weirdpath")
    assert wilder == "weirdpath"

    empty = _invoice_id_from_url("")
    assert empty == "invoice"


async def test_parse_seen_urls_handles_malformed_state() -> None:
    from app.services.scrapers.cursor import _parse_seen_urls

    assert _parse_seen_urls(None) == set()
    assert _parse_seen_urls("") == set()
    assert _parse_seen_urls("not-json{") == set()
    assert _parse_seen_urls("[1,2,3]") == set()
    assert _parse_seen_urls(json.dumps({"seen_urls": "not-a-list"})) == set()
    assert _parse_seen_urls(json.dumps({})) == set()
    assert _parse_seen_urls(json.dumps({"seen_urls": [INVOICE_URL_1]})) == {INVOICE_URL_1}


async def test_wait_for_load_state_timeout_on_stripe_is_non_fatal(
    tmp_path: Any,
) -> None:
    stripe_page = _make_stripe_page(
        tmp_path=tmp_path,
        portal_html=_portal_html([INVOICE_URL_1]),
        invoice_pages={
            INVOICE_URL_1: {"text": _invoice_page_text("May 7, 2026", "$10.00")},
        },
    )
    stripe_page.wait_for_load_state = AsyncMock(
        side_effect=cursor_mod.PlaywrightTimeoutError("slow portal")
    )
    cursor_page = _make_cursor_page(stripe_page=stripe_page)
    scraper = CursorScraper(session_cls=_FakeSession(cursor_page))

    result = await scraper.scan(_make_account())

    assert len(result) == 1


async def test_caps_seen_urls_with_fifo_eviction(tmp_path: Any) -> None:
    prior_urls = [
        f"https://invoice.stripe.com/i/acct_x/live_legacy{i:04d}?s=il&"
        for i in range(SEEN_URLS_CAP)
    ]
    prior_state = json.dumps({
        "_format": "cursor_stripe_v1",
        "seen_urls": prior_urls,
        "last_scan_at": "2026-04-02T00:00:00Z",
    })
    new_urls = [INVOICE_URL_1, INVOICE_URL_2, INVOICE_URL_3]
    stripe_page = _make_stripe_page(
        tmp_path=tmp_path,
        portal_html=_portal_html(new_urls),
        invoice_pages={
            u: {"text": _invoice_page_text("May 7, 2026", "$10.00")} for u in new_urls
        },
    )
    cursor_page = _make_cursor_page(stripe_page=stripe_page)
    scraper = CursorScraper(session_cls=_FakeSession(cursor_page))

    await scraper.scan(_make_account(), last_uid=prior_state)

    state = json.loads(scraper._last_scan_state or "{}")
    seen = state["seen_urls"]
    assert len(seen) == SEEN_URLS_CAP
    assert INVOICE_URL_3 in seen
    assert INVOICE_URL_1 in seen


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
